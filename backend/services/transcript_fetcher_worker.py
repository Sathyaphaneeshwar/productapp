import threading
import time
from datetime import datetime, timedelta

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

    def _compute_next_check(self, status: str, event_date: str, attempts: int) -> datetime:
        now = datetime.now()
        if status == "available":
            return now + timedelta(hours=12)
        if status == "upcoming" and event_date:
            try:
                event_dt = datetime.fromisoformat(str(event_date).replace("Z", "+00:00"))
            except Exception:
                event_dt = None
            if event_dt:
                delta = event_dt - now
                if delta.total_seconds() <= 24 * 3600:
                    return now + timedelta(minutes=10)
                if delta.total_seconds() <= 7 * 24 * 3600:
                    return now + timedelta(minutes=60)
        if status == "error":
            backoff = compute_backoff_seconds(attempts)
            return now + timedelta(seconds=backoff)
        return now + timedelta(hours=4)

    def _process_job(self, job: dict):
        stock_id = job.get("stock_id")
        quarter = job.get("quarter")
        year = job.get("year")
        if not stock_id or not quarter or not year:
            return

        # Phase 1: Read stock info and mark checking (short-lived connection)
        conn = self.get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, stock_symbol, bse_code FROM stocks WHERE id = ?", (stock_id,))
            stock = cursor.fetchone()
            if not stock:
                return
            symbol = stock["stock_symbol"] or stock["bse_code"]
            if not symbol:
                return
            self._mark_check_status(cursor, stock_id, "checking")
            conn.commit()
        finally:
            conn.close()

        # Phase 2: API calls (no DB connection held)
        try:
            available = self.transcript_service.fetch_available_transcripts(symbol)
            available = [t for t in available if t.quarter == quarter and t.year == year]

            upcoming = self.transcript_service.get_upcoming_calls(symbol)
            upcoming = [t for t in upcoming if t.quarter == quarter and t.year == year]
        except Exception as e:
            self._handle_fetch_error(stock_id, quarter, year, e)
            return

        # Phase 3: Persist results (short-lived connection)
        schedule_status = "none"
        event_date = None
        trigger_analysis_transcript_id = None

        conn = self.get_db_connection()
        try:
            cursor = conn.cursor()

            if available:
                transcript = available[0]
                schedule_status = "available"

                cursor.execute(
                    """
                    SELECT id, status, source_url FROM transcripts
                    WHERE stock_id = ? AND quarter = ? AND year = ? LIMIT 1
                    """,
                    (stock_id, quarter, year),
                )
                existing = cursor.fetchone()
                if not existing:
                    cursor.execute(
                        """
                        INSERT INTO transcripts (stock_id, quarter, year, source_url, status)
                        VALUES (?, ?, ?, ?, 'available')
                        """,
                        (stock_id, quarter, year, transcript.source_url),
                    )
                    transcript_id = cursor.lastrowid
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

                cursor.execute(
                    """
                    INSERT INTO transcript_events (stock_id, quarter, year, status, source_url, origin)
                    VALUES (?, ?, ?, 'available', ?, 'poll')
                    """,
                    (stock_id, quarter, year, transcript.source_url),
                )

                # Check eligibility for auto-analysis
                cursor.execute("SELECT 1 FROM watchlist_items WHERE stock_id = ? LIMIT 1", (stock_id,))
                in_watchlist = cursor.fetchone() is not None
                cursor.execute(
                    """
                    SELECT 1 FROM group_stocks gs JOIN groups g ON g.id = gs.group_id
                    WHERE gs.stock_id = ? AND g.is_active = 1 LIMIT 1
                    """,
                    (stock_id,),
                )
                in_active_group = cursor.fetchone() is not None
                if in_watchlist and not in_active_group:
                    trigger_analysis_transcript_id = transcript_id

            elif upcoming:
                call = upcoming[0]
                schedule_status = "upcoming"
                event_date = call.event_date
                cursor.execute(
                    """
                    SELECT id, status, event_date FROM transcripts
                    WHERE stock_id = ? AND quarter = ? AND year = ? LIMIT 1
                    """,
                    (stock_id, quarter, year),
                )
                existing = cursor.fetchone()
                if not existing:
                    cursor.execute(
                        """
                        INSERT INTO transcripts (stock_id, quarter, year, status, event_date)
                        VALUES (?, ?, ?, 'upcoming', ?)
                        """,
                        (stock_id, quarter, year, call.event_date),
                    )
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

                cursor.execute(
                    """
                    INSERT INTO transcript_events (stock_id, quarter, year, status, event_date, origin)
                    VALUES (?, ?, ?, 'upcoming', ?, 'poll')
                    """,
                    (stock_id, quarter, year, call.event_date),
                )

            # Update schedule row
            cursor.execute(
                "SELECT attempts FROM transcript_fetch_schedule WHERE stock_id = ? AND quarter = ? AND year = ?",
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

            self._mark_check_status(cursor, stock_id, "idle")
            conn.commit()
        except Exception as e:
            conn.close()
            self._handle_fetch_error(stock_id, quarter, year, e)
            return
        else:
            conn.close()

        # Phase 4: Trigger analysis (separate connection via AnalysisJobService)
        if trigger_analysis_transcript_id:
            try:
                self.analysis_job_service.enqueue_for_transcript(trigger_analysis_transcript_id)
            except Exception as e:
                print(f"[FetcherWorker] Analysis enqueue failed for stock {stock_id}: {e}")

    def _handle_fetch_error(self, stock_id: int, quarter: str, year: int, error: Exception):
        conn = self.get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT attempts FROM transcript_fetch_schedule WHERE stock_id = ? AND quarter = ? AND year = ?",
                (stock_id, quarter, year),
            )
            sched = cursor.fetchone()
            attempts = (sched["attempts"] if sched else 0) + 1
            next_check_at = self._compute_next_check("error", None, attempts)
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
        except Exception as inner_e:
            print(f"[FetcherWorker] Error handler failed for stock {stock_id}: {inner_e}")
        finally:
            conn.close()
        print(f"[FetcherWorker] Error processing stock {stock_id}: {error}")
