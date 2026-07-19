import threading
import time
import json
from datetime import datetime, timedelta, timezone
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
    def __init__(self, *, schedule_sync_seconds: int = 60, enqueue_seconds: int = 300, group_check_seconds: int = 300):
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
        self.next_enqueue_at = None

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

            for stock_id in all_ids:
                priority = 100 if stock_id in watchlist_ids else 50
                cursor.execute(
                    """
                    INSERT INTO transcript_fetch_schedule (
                        stock_id, quarter, year, priority, next_check_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(stock_id, quarter, year) DO UPDATE SET
                        priority = excluded.priority,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (stock_id, quarter, year, priority),
                )

            # Self-heal stale watchlist rows that were previously scheduled with
            # long "none" cadence even though an upcoming event is near/past.
            # This ensures automatic refresh kicks back in without manual trigger.
            cursor.execute(
                """
                UPDATE transcript_fetch_schedule
                SET next_check_at = CURRENT_TIMESTAMP,
                    attempts = 0,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE quarter = ? AND year = ?
                  AND stock_id IN (SELECT stock_id FROM watchlist_items)
                  AND next_check_at IS NOT NULL
                  AND datetime(next_check_at) > datetime('now', '+30 minutes')
                  AND EXISTS (
                        SELECT 1
                        FROM transcripts t
                        WHERE t.stock_id = transcript_fetch_schedule.stock_id
                          AND t.quarter = transcript_fetch_schedule.quarter
                          AND t.year = transcript_fetch_schedule.year
                          AND t.status = 'upcoming'
                          AND t.event_date IS NOT NULL
                          AND datetime(t.event_date) <= datetime('now', '+24 hours')
                  )
                """,
                (quarter, year),
            )

            deleted_stale, deleted_duplicates = self._cleanup_transcript_check_queue(
                cursor,
                quarter,
                year,
                all_ids,
            )
            if deleted_stale or deleted_duplicates:
                print(
                    "[QueueScheduler] Cleaned transcript_check queue: "
                    f"{deleted_stale} stale, {deleted_duplicates} duplicate"
                )

            conn.commit()
        finally:
            conn.close()

    def _delete_queue_message_ids(self, cursor, message_ids):
        if not message_ids:
            return
        for index in range(0, len(message_ids), 500):
            batch = message_ids[index:index + 500]
            placeholders = ",".join("?" for _ in batch)
            cursor.execute(
                f"DELETE FROM queue_messages WHERE id IN ({placeholders})",
                batch,
            )

    def _decode_transcript_check_payload(self, row):
        try:
            payload = json.loads(row["payload_json"] or "{}")
            stock_id = int(payload.get("stock_id"))
            payload_year = int(payload.get("year"))
            payload_quarter = str(payload.get("quarter") or "")
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return stock_id, payload_quarter, payload_year

    def _cleanup_transcript_check_queue(self, cursor, quarter: str, year: int, active_stock_ids: set[int]):
        cursor.execute(
            """
            SELECT id, payload_json
            FROM queue_messages
            WHERE queue_name = 'transcript_check'
              AND status IN ('pending', 'claimed')
            ORDER BY id ASC
            """
        )
        rows = cursor.fetchall()
        seen = set()
        stale_ids = []
        duplicate_ids = []

        for row in rows:
            decoded = self._decode_transcript_check_payload(row)
            if decoded is None:
                stale_ids.append(row["id"])
                continue

            stock_id, payload_quarter, payload_year = decoded
            if (
                payload_quarter != quarter
                or payload_year != year
                or stock_id not in active_stock_ids
            ):
                stale_ids.append(row["id"])
                continue

            key = (stock_id, payload_quarter, payload_year)
            if key in seen:
                duplicate_ids.append(row["id"])
                continue
            seen.add(key)

        self._delete_queue_message_ids(cursor, stale_ids)
        self._delete_queue_message_ids(cursor, duplicate_ids)
        return len(stale_ids), len(duplicate_ids)

    def _get_queued_transcript_check_stock_ids(self, cursor, quarter: str, year: int) -> set[int]:
        cursor.execute(
            """
            SELECT payload_json
            FROM queue_messages
            WHERE queue_name = 'transcript_check'
              AND status IN ('pending', 'claimed')
            """
        )
        queued_stock_ids = set()
        for row in cursor.fetchall():
            decoded = self._decode_transcript_check_payload(row)
            if decoded is None:
                continue
            stock_id, payload_quarter, payload_year = decoded
            if payload_quarter == quarter and payload_year == year:
                queued_stock_ids.add(stock_id)
        return queued_stock_ids

    def _enqueue_due_transcript_checks(self, quarter: str, year: int):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            now = datetime.utcnow()
            queued_stock_ids = self._get_queued_transcript_check_stock_ids(cursor, quarter, year)
            cursor.execute(
                """
                SELECT id, stock_id, priority
                FROM transcript_fetch_schedule
                WHERE quarter = ? AND year = ?
                  AND (next_check_at IS NULL OR next_check_at <= ?)
                  AND (locked_until IS NULL OR locked_until < ?)
                ORDER BY priority DESC, next_check_at ASC
                LIMIT 1000
                """,
                (quarter, year, now, now),
            )
            rows = cursor.fetchall()
            lock_until = now + timedelta(seconds=120)
            queue_rows = []
            for row in rows:
                if row["stock_id"] in queued_stock_ids:
                    cursor.execute(
                        """
                        UPDATE transcript_fetch_schedule
                        SET locked_until = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (lock_until, row["id"]),
                    )
                    continue

                cursor.execute(
                    """
                    UPDATE transcript_fetch_schedule
                    SET locked_until = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (lock_until, row["id"]),
                )
                queue_rows.append(
                    (
                        "transcript_check",
                        json.dumps(
                            {
                                "stock_id": row["stock_id"],
                                "priority": row["priority"],
                                "quarter": quarter,
                                "year": year,
                                "reason": "scheduled",
                            }
                        ),
                        f"transcript:{row['stock_id']}:{quarter}:{year}",
                    )
                )
                queued_stock_ids.add(row["stock_id"])
            if queue_rows:
                cursor.executemany(
                    """
                    INSERT OR IGNORE INTO queue_messages (
                        queue_name, payload_json, available_at, status,
                        dedupe_key, created_at, updated_at
                    )
                    VALUES (?, ?, CURRENT_TIMESTAMP, 'pending', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    queue_rows,
                )
            conn.commit()
            if queue_rows:
                self.queue.notify("transcript_check")
        finally:
            conn.close()

    def _enqueue_due_analysis_jobs(self):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            now = datetime.utcnow()
            cursor.execute(
                """
                UPDATE analysis_jobs
                SET status = 'retrying',
                    retry_next_at = CURRENT_TIMESTAMP,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'in_progress'
                  AND (locked_until IS NULL OR locked_until < ?)
                """,
                (now,),
            )
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
            queue_rows = []
            for row in rows:
                cursor.execute(
                    """
                    UPDATE analysis_jobs
                    SET locked_until = ?, status = 'queued', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (lock_until, row["id"]),
                )
                queue_rows.append(
                    (
                        "analysis",
                        json.dumps({"analysis_job_id": row["id"]}),
                        f"analysis:{row['id']}",
                    )
                )
            if queue_rows:
                cursor.executemany(
                    """
                    INSERT OR IGNORE INTO queue_messages (
                        queue_name, payload_json, available_at, status,
                        dedupe_key, created_at, updated_at
                    )
                    VALUES (?, ?, CURRENT_TIMESTAMP, 'pending', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    queue_rows,
                )
            conn.commit()
            if queue_rows:
                self.queue.notify("analysis")
        finally:
            conn.close()

    def _enqueue_due_email_jobs(self):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            now = datetime.utcnow()
            cursor.execute(
                """
                UPDATE email_outbox
                SET status = 'retrying',
                    retry_next_at = CURRENT_TIMESTAMP,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'in_progress'
                  AND (locked_until IS NULL OR locked_until < ?)
                """,
                (now,),
            )
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
            queue_rows = []
            for row in rows:
                cursor.execute(
                    """
                    UPDATE email_outbox
                    SET locked_until = ?, status = 'queued', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (lock_until, row["id"]),
                )
                queue_rows.append(
                    (
                        "email",
                        json.dumps({"email_outbox_id": row["id"]}),
                        f"email:{row['id']}",
                    )
                )
            if queue_rows:
                cursor.executemany(
                    """
                    INSERT OR IGNORE INTO queue_messages (
                        queue_name, payload_json, available_at, status,
                        dedupe_key, created_at, updated_at
                    )
                    VALUES (?, ?, CURRENT_TIMESTAMP, 'pending', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    queue_rows,
                )
            conn.commit()
            if queue_rows:
                self.queue.notify("email")
        finally:
            conn.close()

    def _maybe_trigger_group_research(self, quarter: str, year: int):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            now = datetime.utcnow()
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
            self.group_research_service.check_and_trigger_runs(
                target_quarter=quarter,
                target_year=year,
            )

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
                SET next_check_at = CURRENT_TIMESTAMP,
                    attempts = 0,
                    last_status = NULL,
                    last_checked_at = NULL,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
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
                        attempts = 0,
                        last_status = NULL,
                        last_checked_at = NULL,
                        locked_until = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (stock_id, target_quarter, target_year, priority),
                )
            conn.commit()
        finally:
            conn.close()

    def _reset_queue_state_for_fresh_run(self, quarter: str, year: int) -> dict:
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM queue_messages")
            deleted_queue_messages = cursor.rowcount

            cursor.execute(
                """
                UPDATE transcript_fetch_schedule
                SET next_check_at = CURRENT_TIMESTAMP,
                    last_status = NULL,
                    last_checked_at = NULL,
                    attempts = 0,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE quarter = ? AND year = ?
                """,
                (quarter, year),
            )
            reset_transcript_schedules = cursor.rowcount

            cursor.execute(
                """
                UPDATE transcript_checks
                SET status = 'idle',
                    updated_at = CURRENT_TIMESTAMP
                WHERE stock_id IN (
                    SELECT stock_id
                    FROM transcript_fetch_schedule
                    WHERE quarter = ? AND year = ?
                )
                """,
                (quarter, year),
            )
            reset_transcript_checks = cursor.rowcount

            cursor.execute(
                """
                UPDATE analysis_jobs
                SET retry_next_at = CURRENT_TIMESTAMP,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('pending', 'queued', 'retrying')
                """
            )
            reset_analysis_jobs = cursor.rowcount

            cursor.execute(
                """
                UPDATE email_outbox
                SET retry_next_at = CURRENT_TIMESTAMP,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('pending', 'queued', 'retrying')
                """
            )
            reset_email_jobs = cursor.rowcount

            conn.commit()
            return {
                "deleted_queue_messages": deleted_queue_messages,
                "reset_transcript_schedules": reset_transcript_schedules,
                "reset_transcript_checks": reset_transcript_checks,
                "reset_analysis_jobs": reset_analysis_jobs,
                "reset_email_jobs": reset_email_jobs,
            }
        finally:
            conn.close()

    def trigger_now(self, *, fresh: bool = False):
        quarter, year = _get_latest_quarter()
        self._sync_schedule(quarter, year)
        reset_summary = None
        if fresh:
            reset_summary = self._reset_queue_state_for_fresh_run(quarter, year)
        else:
            conn = self.get_db_connection()
            try:
                conn.execute(
                    """
                    UPDATE transcript_fetch_schedule
                    SET next_check_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE quarter = ? AND year = ?
                      AND locked_until IS NULL
                      AND COALESCE(last_status, '') != 'failed'
                    """,
                    (quarter, year),
                )
                conn.commit()
            finally:
                conn.close()
        self._enqueue_due_transcript_checks(quarter, year)
        self._enqueue_due_analysis_jobs()
        self._enqueue_due_email_jobs()
        self.last_enqueue = datetime.utcnow()
        self.next_enqueue_at = self.last_enqueue + timedelta(seconds=self.enqueue_seconds)
        return {
            "fresh": fresh,
            "quarter": quarter,
            "year": year,
            "reset": reset_summary,
        }

    def get_status(self) -> dict:
        def _as_utc_datetime(value):
            if value is None:
                return None
            if isinstance(value, datetime):
                parsed = value
            else:
                raw = str(value).strip()
                if not raw:
                    return None
                try:
                    parsed = datetime.fromisoformat(
                        raw.replace(" ", "T").replace("Z", "+00:00")
                    )
                except ValueError:
                    return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        def _iso(value):
            parsed = _as_utc_datetime(value)
            return parsed.isoformat().replace("+00:00", "Z") if parsed else None

        def _epoch_ms(value):
            parsed = _as_utc_datetime(value)
            return int(parsed.timestamp() * 1000) if parsed else None

        now_utc = datetime.now(timezone.utc)
        next_tick = self.next_enqueue_at if self.running else None
        if self.running and next_tick is None:
            next_tick = datetime.utcnow()

        queues = {"transcript_check": 0, "analysis": 0, "email": 0}
        queue_claimed = {"transcript_check": 0, "analysis": 0, "email": 0}
        active_transcript_checks = 0
        next_transcript_check_at = None
        waiting_analysis = 0
        active_analysis = 0
        failed_analysis = 0
        waiting_email = 0
        active_email = 0
        failed_email = 0
        failed_transcript_checks = 0
        watching_count = 0
        upcoming_count = 0
        current_analysis = None
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT queue_name, status, COUNT(*) AS count
                FROM queue_messages
                WHERE status IN ('pending', 'claimed')
                GROUP BY queue_name, status
                """
            )
            for row in cursor.fetchall():
                queue_name = row["queue_name"]
                if queue_name not in queues:
                    continue
                count = int(row["count"])
                queues[queue_name] += count
                if row["status"] == "claimed":
                    queue_claimed[queue_name] += count

            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM transcript_fetch_schedule
                WHERE locked_until IS NOT NULL
                  AND datetime(locked_until) > datetime('now')
                """
            )
            row = cursor.fetchone()
            active_transcript_checks = int(row["count"]) if row else 0

            cursor.execute(
                """
                SELECT MIN(next_check_at) AS next_check_at
                FROM transcript_fetch_schedule
                WHERE next_check_at IS NOT NULL
                """
            )
            row = cursor.fetchone()
            next_transcript_check_at = row["next_check_at"] if row else None

            cursor.execute(
                """
                SELECT
                    SUM(CASE WHEN status IN ('pending', 'queued', 'retrying') THEN 1 ELSE 0 END) AS waiting,
                    SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) AS active,
                    SUM(CASE WHEN status IN ('failed', 'error') THEN 1 ELSE 0 END) AS failed
                FROM analysis_jobs
                """
            )
            row = cursor.fetchone()
            waiting_analysis = int(row["waiting"] or 0)
            active_analysis = int(row["active"] or 0)
            failed_analysis = int(row["failed"] or 0)

            cursor.execute(
                """
                SELECT
                    SUM(CASE WHEN status IN ('pending', 'queued', 'retrying') THEN 1 ELSE 0 END) AS waiting,
                    SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) AS active,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
                FROM email_outbox
                """
            )
            row = cursor.fetchone()
            waiting_email = int(row["waiting"] or 0)
            active_email = int(row["active"] or 0)
            failed_email = int(row["failed"] or 0)

            cursor.execute(
                """
                SELECT
                    COUNT(*) AS watching,
                    SUM(CASE WHEN last_status = 'failed' THEN 1 ELSE 0 END) AS failed
                FROM transcript_fetch_schedule
                """
            )
            row = cursor.fetchone()
            watching_count = int(row["watching"] or 0)
            failed_transcript_checks = int(row["failed"] or 0)

            cursor.execute(
                "SELECT COUNT(*) AS count FROM transcripts WHERE status = 'upcoming'"
            )
            row = cursor.fetchone()
            upcoming_count = int(row["count"] or 0)

            cursor.execute(
                """
                SELECT COALESCE(s.stock_symbol, s.bse_code, s.isin_number) AS symbol,
                       t.quarter, t.year
                FROM analysis_jobs aj
                JOIN transcripts t ON t.id = aj.transcript_id
                JOIN stocks s ON s.id = t.stock_id
                WHERE aj.status = 'in_progress'
                ORDER BY aj.updated_at ASC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row:
                current_analysis = f"{row['symbol']} {row['quarter']} FY{row['year']}"
        except Exception as error:
            print(f"[QueueScheduler] Status query failed: {error}")
        finally:
            conn.close()

        is_polling = (
            queues["transcript_check"] > 0
            or active_transcript_checks > 0
            or active_analysis > 0
            or active_email > 0
        )
        failed_total = failed_transcript_checks + failed_analysis + failed_email
        next_check_ms = _epoch_ms(next_transcript_check_at)
        next_tick_ms = _epoch_ms(next_tick)
        next_display_ms = next_check_ms if next_check_ms is not None else next_tick_ms
        next_poll_in_seconds = None
        if next_display_ms is not None:
            next_poll_in_seconds = max(
                0,
                int((next_display_ms - int(now_utc.timestamp() * 1000) + 999) / 1000),
            )

        return {
            "running": self.running,
            "scheduler_running": self.running,
            "is_polling": is_polling,
            "poll_interval_seconds": self.enqueue_seconds,
            "server_now_ms": int(now_utc.timestamp() * 1000),
            "next_check_at_ms": next_check_ms,
            "next_tick_at_ms": next_tick_ms,
            "next_poll_at": _iso(next_tick),
            "next_poll_in_seconds": next_poll_in_seconds,
            "queue_ok": self.queue.ping(),
            "queues": queues,
            "queue_claimed": queue_claimed,
            "active_transcript_checks": active_transcript_checks,
            "next_transcript_check_at": _iso(next_transcript_check_at),
            "analysis": {
                "waiting": waiting_analysis,
                "active": active_analysis,
                "failed": failed_analysis,
                "current": current_analysis,
            },
            "email": {
                "waiting": waiting_email,
                "active": active_email,
                "failed": failed_email,
            },
            "transcripts": {
                "watching": watching_count,
                "upcoming": upcoming_count,
                "failed": failed_transcript_checks,
            },
            "failed_total": failed_total,
            "workers": {
                "scheduler": bool(self.thread and self.thread.is_alive()),
            },
            "last_schedule_sync": _iso(self.last_schedule_sync),
            "last_schedule_sync_ms": _epoch_ms(self.last_schedule_sync),
            "last_enqueue": _iso(self.last_enqueue),
            "last_group_check": _iso(self.last_group_check),
        }

    def _run(self):
        next_schedule_sync = time.monotonic()
        next_enqueue = time.monotonic()
        next_group_check = time.monotonic() + self.group_check_seconds

        while self.running:
            try:
                now_monotonic = time.monotonic()
                now_utc = datetime.utcnow()
                quarter, year = _get_latest_quarter()

                if now_monotonic >= next_schedule_sync:
                    self._sync_schedule(quarter, year)
                    self.last_schedule_sync = now_utc
                    next_schedule_sync = now_monotonic + self.schedule_sync_seconds

                if now_monotonic >= next_enqueue:
                    self._enqueue_due_transcript_checks(quarter, year)
                    self._enqueue_due_analysis_jobs()
                    self._enqueue_due_email_jobs()
                    self.last_enqueue = now_utc
                    next_enqueue = now_monotonic + self.enqueue_seconds
                    self.next_enqueue_at = now_utc + timedelta(seconds=self.enqueue_seconds)

                if now_monotonic >= next_group_check:
                    self._maybe_trigger_group_research(quarter, year)
                    self.last_group_check = now_utc
                    next_group_check = now_monotonic + self.group_check_seconds
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
