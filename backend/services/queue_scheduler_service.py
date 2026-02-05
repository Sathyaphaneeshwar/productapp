import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from config import DATABASE_PATH
from db import get_db_connection
from services.queue_service import QueueService
from services.group_research_service import GroupResearchService


def _get_latest_quarter():
    now = datetime.now()
    month = now.month
    year = now.year

    if 4 <= month <= 6:
        current_q, current_fy = "Q1", year + 1
    elif 7 <= month <= 9:
        current_q, current_fy = "Q2", year + 1
    elif 10 <= month <= 12:
        current_q, current_fy = "Q3", year + 1
    else:
        current_q, current_fy = "Q4", year

    if current_q == "Q1":
        return "Q4", current_fy - 1
    if current_q == "Q2":
        return "Q1", current_fy
    if current_q == "Q3":
        return "Q2", current_fy
    return "Q3", current_fy


class QueueSchedulerService:
    def __init__(self, *, schedule_sync_seconds: int = 60, enqueue_seconds: int = 5, group_check_seconds: int = 300):
        self.queue = QueueService()
        self.group_research_service = GroupResearchService()
        self.running = False
        self.thread = None
        self.schedule_sync_seconds = schedule_sync_seconds
        self.enqueue_seconds = enqueue_seconds
        self.group_check_seconds = group_check_seconds
        self.last_schedule_sync = None
        self.last_group_check = None
        self.last_enqueue = None

    def get_db_connection(self):
        return get_db_connection(DATABASE_PATH)

    def _sync_schedule(self, quarter: str, year: int):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT s.id
                FROM stocks s
                INNER JOIN watchlist_items w ON s.id = w.stock_id
                """
            )
            watchlist_ids = {row["id"] for row in cursor.fetchall()}

            cursor.execute(
                """
                SELECT DISTINCT s.id
                FROM stocks s
                INNER JOIN group_stocks gs ON s.id = gs.stock_id
                INNER JOIN groups g ON g.id = gs.group_id
                WHERE g.is_active = 1
                """
            )
            group_ids = {row["id"] for row in cursor.fetchall()}

            all_ids = watchlist_ids | group_ids

            # Remove schedule entries for stocks no longer in watchlist/groups
            if all_ids:
                placeholders = ",".join("?" for _ in all_ids)
                cursor.execute(
                    f"""
                    DELETE FROM transcript_fetch_schedule
                    WHERE (quarter != ? OR year != ?) OR stock_id NOT IN ({placeholders})
                    """,
                    (quarter, year, *all_ids),
                )
            else:
                cursor.execute("DELETE FROM transcript_fetch_schedule")

            now = datetime.now().isoformat(sep=" ", timespec="seconds")
            for stock_id in all_ids:
                priority = 100 if stock_id in watchlist_ids else 50
                cursor.execute(
                    """
                    INSERT INTO transcript_fetch_schedule (
                        stock_id, quarter, year, priority, next_check_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(stock_id, quarter, year) DO UPDATE SET
                        priority = excluded.priority,
                        next_check_at = COALESCE(transcript_fetch_schedule.next_check_at, excluded.next_check_at),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (stock_id, quarter, year, priority, now),
                )

            conn.commit()
        finally:
            conn.close()

    def _enqueue_due_transcript_checks(self, quarter: str, year: int):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            now = datetime.now()
            cursor.execute(
                """
                SELECT id, stock_id, priority
                FROM transcript_fetch_schedule
                WHERE quarter = ? AND year = ?
                  AND (next_check_at IS NULL OR next_check_at <= ?)
                  AND (locked_until IS NULL OR locked_until < ?)
                ORDER BY priority DESC, next_check_at ASC
                LIMIT 100
                """,
                (quarter, year, now, now),
            )
            rows = cursor.fetchall()
            lock_until = now + timedelta(seconds=120)
            for row in rows:
                cursor.execute(
                    """
                    UPDATE transcript_fetch_schedule
                    SET locked_until = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (lock_until, row["id"]),
                )
                self.queue.enqueue(
                    "transcript_check",
                    {
                        "stock_id": row["stock_id"],
                        "priority": row["priority"],
                        "quarter": quarter,
                        "year": year,
                        "reason": "scheduled",
                    },
                )
            conn.commit()
        finally:
            conn.close()

    def _enqueue_due_analysis_jobs(self):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            now = datetime.now()
            cursor.execute(
                """
                SELECT id
                FROM analysis_jobs
                WHERE status IN ('pending', 'retrying', 'queued')
                  AND (retry_next_at IS NULL OR retry_next_at <= ?)
                  AND (locked_until IS NULL OR locked_until < ?)
                ORDER BY created_at ASC
                LIMIT 100
                """,
                (now, now),
            )
            rows = cursor.fetchall()
            lock_until = now + timedelta(seconds=900)
            for row in rows:
                cursor.execute(
                    """
                    UPDATE analysis_jobs
                    SET locked_until = ?, status = 'queued', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (lock_until, row["id"]),
                )
                self.queue.enqueue("analysis", {"analysis_job_id": row["id"]})
            conn.commit()
        finally:
            conn.close()

    def _enqueue_due_email_jobs(self):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            now = datetime.now()
            cursor.execute(
                """
                SELECT id
                FROM email_outbox
                WHERE status IN ('pending', 'retrying', 'queued')
                  AND (retry_next_at IS NULL OR retry_next_at <= ?)
                  AND (locked_until IS NULL OR locked_until < ?)
                ORDER BY scheduled_at ASC
                LIMIT 200
                """,
                (now, now),
            )
            rows = cursor.fetchall()
            lock_until = now + timedelta(seconds=900)
            for row in rows:
                cursor.execute(
                    """
                    UPDATE email_outbox
                    SET locked_until = ?, status = 'queued', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (lock_until, row["id"]),
                )
                self.queue.enqueue("email", {"email_outbox_id": row["id"]})
            conn.commit()
        finally:
            conn.close()

    def _maybe_trigger_group_research(self, quarter: str, year: int):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            now = datetime.now()
            cursor.execute(
                """
                SELECT 1
                FROM transcript_fetch_schedule tfs
                JOIN watchlist_items w ON w.stock_id = tfs.stock_id
                WHERE tfs.quarter = ? AND tfs.year = ?
                  AND (tfs.next_check_at <= ? OR (tfs.locked_until IS NOT NULL AND tfs.locked_until > ?))
                LIMIT 1
                """,
                (quarter, year, now, now),
            )
            still_processing = cursor.fetchone() is not None
        finally:
            conn.close()

        if not still_processing:
            self.group_research_service.check_and_trigger_runs()

    def trigger_for_stock(self, stock_id: int, quarter: Optional[str] = None, year: Optional[int] = None):
        target_quarter, target_year = _get_latest_quarter()
        if quarter and year:
            target_quarter, target_year = quarter, year
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE transcript_fetch_schedule
                SET next_check_at = CURRENT_TIMESTAMP, attempts = 0, locked_until = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE stock_id = ? AND quarter = ? AND year = ?
                """,
                (stock_id, target_quarter, target_year),
            )
            if cursor.rowcount == 0:
                cursor.execute("SELECT 1 FROM watchlist_items WHERE stock_id = ? LIMIT 1", (stock_id,))
                in_watchlist = cursor.fetchone() is not None
                priority = 100 if in_watchlist else 50
                cursor.execute(
                    """
                    INSERT INTO transcript_fetch_schedule (stock_id, quarter, year, priority, next_check_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(stock_id, quarter, year) DO UPDATE SET
                        priority = excluded.priority,
                        next_check_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (stock_id, target_quarter, target_year, priority),
                )
            conn.commit()
        finally:
            conn.close()

    def trigger_now(self):
        quarter, year = _get_latest_quarter()
        self._sync_schedule(quarter, year)
        self._enqueue_due_transcript_checks(quarter, year)

    def get_status(self) -> dict:
        return {
            "running": self.running,
            "queue_ok": self.queue.ping(),
            "queues": {
                "transcript_check": self.queue.length("transcript_check"),
                "analysis": self.queue.length("analysis"),
                "email": self.queue.length("email"),
            },
            "last_schedule_sync": self.last_schedule_sync.isoformat() if self.last_schedule_sync else None,
            "last_enqueue": self.last_enqueue.isoformat() if self.last_enqueue else None,
            "last_group_check": self.last_group_check.isoformat() if self.last_group_check else None,
        }

    def _run(self):
        next_schedule_sync = datetime.now()
        next_enqueue = datetime.now()
        next_group_check = datetime.now() + timedelta(seconds=self.group_check_seconds)

        while self.running:
            try:
                now = datetime.now()
                quarter, year = _get_latest_quarter()

                if now >= next_schedule_sync:
                    self._sync_schedule(quarter, year)
                    self.last_schedule_sync = now
                    next_schedule_sync = now + timedelta(seconds=self.schedule_sync_seconds)

                if now >= next_enqueue:
                    self._enqueue_due_transcript_checks(quarter, year)
                    self._enqueue_due_analysis_jobs()
                    self._enqueue_due_email_jobs()
                    self.last_enqueue = now
                    next_enqueue = now + timedelta(seconds=self.enqueue_seconds)

                if now >= next_group_check:
                    self._maybe_trigger_group_research(quarter, year)
                    self.last_group_check = now
                    next_group_check = now + timedelta(seconds=self.group_check_seconds)
            except Exception as e:
                print(f"[QueueScheduler] Error in loop: {e}")

            time.sleep(1)

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
