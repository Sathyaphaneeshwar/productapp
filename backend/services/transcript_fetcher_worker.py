import threading
import time
from datetime import datetime, timedelta, timezone

from config import DATABASE_PATH
from db import get_db_connection
from services.queue_service import QueueService
from services.transcript_service import TranscriptService
from services.analysis_job_service import AnalysisJobService
from services.stock_activity_service import StockActivityService

MAX_TRANSCRIPT_ERROR_ATTEMPTS = 8


def _safe_print(message: str):
    """Print that can never raise (Windows pipes may reject Unicode)."""
    try:
        print(message)
    except Exception:
        pass


class TranscriptFetcherWorker:
    def __init__(self):
        self.queue = QueueService()
        self.transcript_service = TranscriptService()
        self.analysis_job_service = AnalysisJobService()
        self.activity_service = StockActivityService()
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
            try:
                job = self.queue.dequeue("transcript_check", timeout=15, lease_seconds=180)
            except Exception as error:
                _safe_print(f"[FetcherWorker] Queue claim failed: {error}")
                time.sleep(5)
                continue
            if not job:
                continue
            try:
                self._process_job(job)
                self.queue.ack(job)
            except Exception as e:
                try:
                    self.queue.release(job, delay_seconds=60, error=str(e))
                except Exception as release_error:
                    _safe_print(f"[FetcherWorker] Job release failed: {release_error}")
                _safe_print(f"[FetcherWorker] Job failed: {e}")

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
            return as_utc_naive(now + timedelta(days=7))
        if status == "upcoming":
            parsed_event = None
            if event_date:
                try:
                    parsed_event = datetime.fromisoformat(
                        str(event_date).strip().replace("Z", "+00:00").replace(" ", "T")
                    )
                    if parsed_event.tzinfo is None:
                        parsed_event = parsed_event.replace(tzinfo=timezone.utc)
                    else:
                        parsed_event = parsed_event.astimezone(timezone.utc)
                except (TypeError, ValueError):
                    parsed_event = None

            if parsed_event is None:
                return as_utc_naive(now + timedelta(hours=6))

            until_event = parsed_event - now
            if until_event > timedelta(days=7):
                delay = timedelta(hours=24)
            elif until_event > timedelta(days=1):
                delay = timedelta(hours=6)
            elif until_event.total_seconds() > 0:
                delay = timedelta(minutes=30)
            elif until_event >= -timedelta(hours=48):
                delay = timedelta(minutes=15)
            elif until_event >= -timedelta(days=7):
                delay = timedelta(hours=1)
            else:
                delay = timedelta(hours=6)
            return as_utc_naive(now + delay)
        if status == "error":
            retry_delays = (
                timedelta(minutes=5),
                timedelta(minutes=15),
                timedelta(hours=1),
                timedelta(hours=6),
                timedelta(hours=12),
                timedelta(hours=24),
            )
            index = min(max(attempts, 1) - 1, len(retry_delays) - 1)
            return as_utc_naive(now + retry_delays[index])
        # "none": nothing found yet. Transcripts often appear within hours of a
        # concall, so a long backoff here makes the app look broken during
        # earnings season. Watchlist stocks recheck fast; group-only stocks
        # recheck moderately.
        if is_watchlist_stock:
            return as_utc_naive(now + timedelta(minutes=30))
        return as_utc_naive(now + timedelta(hours=2))

    def _process_job(self, job: dict):
        stock_id = job.get("stock_id")
        quarter = job.get("quarter")
        year = job.get("year")
        if not stock_id or not quarter or not year:
            return

        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, stock_symbol, bse_code, isin_number, stock_name
                FROM stocks
                WHERE id = ?
                """,
                (stock_id,),
            )
            stock = cursor.fetchone()
            if not stock:
                raise ValueError("Stock not found")

            symbol = (
                stock["stock_symbol"]
                or stock["bse_code"]
                or stock["isin_number"]
            )
            if not symbol:
                raise ValueError("Stock has no usable ISIN or exchange symbol")

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
            cursor.execute(
                """
                SELECT last_status
                FROM transcript_fetch_schedule
                WHERE stock_id = ? AND quarter = ? AND year = ?
                """,
                (stock_id, quarter, year),
            )
            schedule_before = cursor.fetchone()
            previous_schedule_status = schedule_before["last_status"] if schedule_before else None

            cursor.execute("SELECT 1 FROM watchlist_items WHERE stock_id = ? LIMIT 1", (stock_id,))
            in_watchlist = cursor.fetchone() is not None

            self._mark_check_status(cursor, stock_id, "checking")
            conn.commit()

            available, pending = self.transcript_service.fetch_concall_states(symbol)
            available = [t for t in available if t.quarter == quarter and t.year == year]
            pending = [t for t in pending if t.quarter == quarter and t.year == year]

            # An available transcript is authoritative. Avoid a second network
            # request that could fail and discard an already-successful result.
            upcoming = []
            if not available:
                if pending:
                    # The call already happened and Tijori has it recorded but the
                    # transcript PDF is not uploaded yet. Treat it like an upcoming
                    # event so polling stays on the fast post-event cadence.
                    upcoming = pending
                else:
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
            next_check_at = self._compute_next_check(
                schedule_status,
                event_date,
                attempts,
                is_watchlist_stock=in_watchlist,
            )

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

            if schedule_status != previous_schedule_status:
                if schedule_status == "available":
                    level = "success"
                    message = "Transcript became available"
                elif schedule_status == "upcoming":
                    level = "info"
                    message = "Earnings call detected; transcript polling is active"
                else:
                    level = "info"
                    message = "Transcript check completed; no transcript found"
                self.activity_service.safe_log_event(
                    stock_id,
                    "transcript",
                    level,
                    message,
                    quarter=quarter,
                    year=year,
                    details={
                        "status": schedule_status,
                        "next_check_at": next_check_at,
                        "event_date": event_date,
                    },
                )

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
            exhausted = attempts >= MAX_TRANSCRIPT_ERROR_ATTEMPTS
            schedule_error_status = "failed" if exhausted else "error"
            if exhausted:
                next_check_at = None
            cursor.execute(
                """
                UPDATE transcript_fetch_schedule
                SET last_status = ?, last_checked_at = CURRENT_TIMESTAMP,
                    next_check_at = ?, attempts = ?, locked_until = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE stock_id = ? AND quarter = ? AND year = ?
                """,
                (
                    schedule_error_status,
                    next_check_at,
                    attempts,
                    stock_id,
                    quarter,
                    year,
                ),
            )
            self._mark_check_status(cursor, stock_id, "idle")
            conn.commit()
            self.activity_service.safe_log_event(
                stock_id,
                "transcript",
                "error",
                (
                    f"Transcript checks paused after {MAX_TRANSCRIPT_ERROR_ATTEMPTS} consecutive failures"
                    if exhausted
                    else f"Transcript check failed: {str(e)[:700]}"
                ),
                quarter=quarter,
                year=year,
                details={
                    "attempt": attempts,
                    "failed": exhausted,
                    "error": str(e)[:700],
                    "next_check_at": next_check_at,
                },
            )
            _safe_print(f"[FetcherWorker] Error processing stock {stock_id}: {e}")
        finally:
            conn.close()
