import threading
import time
from datetime import datetime, timedelta, timezone

from config import DATABASE_PATH
from db import get_db_connection
from services.queue_service import QueueService
from services.transcript_service import TranscriptService
from services.analysis_job_service import AnalysisJobService
from services.retry_utils import compute_backoff_seconds


class TranscriptFetcherWorker:
    def __init__(self):
        self.queue = QueueService()
        self.transcript_service = TranscriptService()
        self.analysis_job_service = AnalysisJobService()
        self.db_path = str(DATABASE_PATH)
        self.running = False
        self.thread = None

    def get_db_connection(self):
        return get_db_connection(self.db_path)

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

    def _run(self):
        while self.running:
            job = self.queue.dequeue("transcript_check", timeout=5)
            if not job:
                continue
            try:
                self._process_job(job)
            except Exception as e:
                print(f"[FetcherWorker] Job failed: {e}")

    def _mark_check_status(self, cursor, stock_id: int, status: str):
        cursor.execute(
            """
            INSERT INTO transcript_checks (stock_id, status, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(stock_id) DO UPDATE SET
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP
            """,
            (stock_id, status),
        )

    def _parse_event_dt(self, value):
        if not value:
            return None
        try:
            event_dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None
        if event_dt.tzinfo is None:
            event_dt = event_dt.replace(tzinfo=timezone.utc)
        else:
            event_dt = event_dt.astimezone(timezone.utc)
        return event_dt

    def _compute_next_check(
        self,
        status: str,
        event_date: str,
        attempts: int,
        *,
        is_watchlist_stock: bool = False,
    ) -> datetime:
        now = datetime.now(timezone.utc)

        def as_utc_naive(value: datetime) -> datetime:
            return value.astimezone(timezone.utc).replace(tzinfo=None)

        if status == "available":
            return as_utc_naive(now + timedelta(hours=12))
        if status == "upcoming":
            # Upcoming cadence rules:
            # - Before event day: hourly checks
            # - Event day and after scheduled time: every 10 minutes
            # - Missing/invalid event date: stay aggressive at 10 minutes
            event_dt = self._parse_event_dt(event_date)
            if event_dt is None:
                return as_utc_naive(now + timedelta(minutes=10))
            if event_dt <= now:
                return as_utc_naive(now + timedelta(minutes=10))
            if event_dt.date() == now.date():
                return as_utc_naive(now + timedelta(minutes=10))
            return as_utc_naive(now + timedelta(hours=1))
        if status == "error":
            backoff = compute_backoff_seconds(attempts)
            # Keep watchlist retries responsive even when transient errors occur.
            if is_watchlist_stock:
                backoff = min(backoff, 10 * 60)
            return as_utc_naive(now + timedelta(seconds=backoff))
        return as_utc_naive(now + timedelta(hours=4))

    def _process_job(self, job: dict):
        stock_id = job.get("stock_id")
        quarter = job.get("quarter")
        year = job.get("year")
        if not stock_id or not quarter or not year:
            return

        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, stock_symbol, bse_code FROM stocks WHERE id = ?", (stock_id,))
            stock = cursor.fetchone()
            if not stock:
                return

            symbol = stock["stock_symbol"] or stock["bse_code"]
            if not symbol:
                return

            cursor.execute(
                """
                SELECT id, status, source_url, event_date
                FROM transcripts
                WHERE stock_id = ? AND quarter = ? AND year = ?
                LIMIT 1
                """,
                (stock_id, quarter, year),
            )
            existing = cursor.fetchone()

            self._mark_check_status(cursor, stock_id, "checking")
            conn.commit()

            available = self.transcript_service.fetch_available_transcripts(symbol)
            available = [t for t in available if t.quarter == quarter and t.year == year]

            upcoming = self.transcript_service.get_upcoming_calls(symbol)
            upcoming = [t for t in upcoming if t.quarter == quarter and t.year == year]

            schedule_status = "none"
            event_date = None

            if available:
                transcript = available[0]
                schedule_status = "available"
                if not existing:
                    cursor.execute(
                        """
                        INSERT INTO transcripts (stock_id, quarter, year, source_url, status)
                        VALUES (?, ?, ?, ?, 'available')
                        """,
                        (stock_id, quarter, year, transcript.source_url),
                    )
                    conn.commit()
                    transcript_id = cursor.lastrowid
                    existing = {
                        "id": transcript_id,
                        "status": "available",
                        "source_url": transcript.source_url,
                        "event_date": None,
                    }
                else:
                    transcript_id = existing["id"]
                    if existing["status"] != "available" or existing["source_url"] != transcript.source_url:
                        cursor.execute(
                            """
                            UPDATE transcripts
                            SET status = 'available', source_url = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (transcript.source_url, transcript_id),
                        )
                        conn.commit()
                        existing = {
                            "id": transcript_id,
                            "status": "available",
                            "source_url": transcript.source_url,
                            "event_date": existing["event_date"],
                        }

                cursor.execute(
                    """
                    INSERT INTO transcript_events (stock_id, quarter, year, status, source_url, origin)
                    VALUES (?, ?, ?, 'available', ?, 'poll')
                    """,
                    (stock_id, quarter, year, transcript.source_url),
                )
                conn.commit()

                # Auto-trigger analysis for watchlist stocks only.
                cursor.execute("SELECT 1 FROM watchlist_items WHERE stock_id = ? LIMIT 1", (stock_id,))
                in_watchlist = cursor.fetchone() is not None
                if in_watchlist:
                    self.analysis_job_service.enqueue_for_transcript(transcript_id)

            elif upcoming:
                call = upcoming[0]
                schedule_status = "upcoming"
                event_date = call.event_date
                if not existing:
                    cursor.execute(
                        """
                        INSERT INTO transcripts (stock_id, quarter, year, status, event_date)
                        VALUES (?, ?, ?, 'upcoming', ?)
                        """,
                        (stock_id, quarter, year, call.event_date),
                    )
                    conn.commit()
                    existing = {
                        "id": cursor.lastrowid,
                        "status": "upcoming",
                        "source_url": None,
                        "event_date": call.event_date,
                    }
                elif existing["status"] == "available" and existing["source_url"]:
                    # Keep available transcripts authoritative even if the upcoming API
                    # also returns a matching row for the same quarter/year.
                    schedule_status = "available"
                    event_date = None
                else:
                    if existing["status"] != "upcoming" or existing["event_date"] != call.event_date:
                        cursor.execute(
                            """
                            UPDATE transcripts
                            SET status = 'upcoming', event_date = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (call.event_date, existing["id"]),
                        )
                        conn.commit()
                        existing = {
                            "id": existing["id"],
                            "status": "upcoming",
                            "source_url": existing["source_url"],
                            "event_date": call.event_date,
                        }

                cursor.execute(
                    """
                    INSERT INTO transcript_events (stock_id, quarter, year, status, event_date, origin)
                    VALUES (?, ?, ?, 'upcoming', ?, 'poll')
                    """,
                    (stock_id, quarter, year, call.event_date),
                )
                conn.commit()
            else:
                # If API temporarily returns neither list, keep an existing upcoming row
                # in upcoming state so polling stays active until transcript appears.
                if existing and existing["status"] == "upcoming":
                    schedule_status = "upcoming"
                    event_date = existing["event_date"]
                else:
                    schedule_status = "none"
                    event_date = None

                cursor.execute(
                    """
                    INSERT INTO transcript_events (stock_id, quarter, year, status, event_date, origin)
                    VALUES (?, ?, ?, ?, ?, 'poll')
                    """,
                    (stock_id, quarter, year, schedule_status, event_date),
                )
                conn.commit()

            # Update schedule row
            cursor.execute(
                """
                SELECT attempts FROM transcript_fetch_schedule
                WHERE stock_id = ? AND quarter = ? AND year = ?
                """,
                (stock_id, quarter, year),
            )
            sched = cursor.fetchone()
            attempts = sched["attempts"] if sched else 0
            next_check_at = self._compute_next_check(schedule_status, event_date, attempts)

            cursor.execute(
                """
                UPDATE transcript_fetch_schedule
                SET last_status = ?, last_checked_at = CURRENT_TIMESTAMP,
                    last_available_at = CASE WHEN ? = 'available' THEN CURRENT_TIMESTAMP ELSE last_available_at END,
                    next_check_at = ?, attempts = 0, locked_until = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE stock_id = ? AND quarter = ? AND year = ?
                """,
                (schedule_status, schedule_status, next_check_at, stock_id, quarter, year),
            )
            conn.commit()

            self._mark_check_status(cursor, stock_id, "idle")
            conn.commit()

        except Exception as e:
            cursor.execute("SELECT 1 FROM watchlist_items WHERE stock_id = ? LIMIT 1", (stock_id,))
            is_watchlist_stock = cursor.fetchone() is not None
            cursor.execute(
                """
                SELECT attempts FROM transcript_fetch_schedule
                WHERE stock_id = ? AND quarter = ? AND year = ?
                """,
                (stock_id, quarter, year),
            )
            sched = cursor.fetchone()
            attempts = (sched["attempts"] if sched else 0) + 1
            next_check_at = self._compute_next_check(
                "error",
                None,
                attempts,
                is_watchlist_stock=is_watchlist_stock,
            )
            cursor.execute(
                """
                UPDATE transcript_fetch_schedule
                SET last_status = 'error', last_checked_at = CURRENT_TIMESTAMP,
                    next_check_at = ?, attempts = ?, locked_until = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE stock_id = ? AND quarter = ? AND year = ?
                """,
                (next_check_at, attempts, stock_id, quarter, year),
            )
            self._mark_check_status(cursor, stock_id, "idle")
            conn.commit()
            print(f"[FetcherWorker] Error processing stock {stock_id}: {e}")
        finally:
            conn.close()
