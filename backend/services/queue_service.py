import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from config import DATABASE_PATH

logger = logging.getLogger(__name__)


class QueueService:
    _events: dict[tuple[str, str], threading.Event] = {}
    _events_lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path or DATABASE_PATH)

    def _get_connection(self, timeout: float = 30.0) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _event_for(self, queue_name: str) -> threading.Event:
        key = (self.db_path, queue_name)
        with self._events_lock:
            event = self._events.get(key)
            if event is None:
                event = threading.Event()
                self._events[key] = event
            return event

    def notify(self, queue_name: str) -> None:
        self._event_for(queue_name).set()

    def enqueue(
        self,
        queue_name: str,
        payload: dict,
        *,
        dedupe_key: Optional[str] = None,
    ) -> bool:
        body = json.dumps(payload)
        attempts = 0
        max_attempts = 5
        while True:
            conn = self._get_connection()
            try:
                result = conn.execute(
                    """
                    INSERT OR IGNORE INTO queue_messages (
                        queue_name, payload_json, available_at, status,
                        dedupe_key, created_at, updated_at
                    )
                    VALUES (?, ?, CURRENT_TIMESTAMP, 'pending', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (queue_name, body, dedupe_key),
                )
                conn.commit()
                inserted = result.rowcount == 1
                if inserted:
                    self.notify(queue_name)
                return inserted
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or attempts >= max_attempts:
                    raise
                attempts += 1
                time.sleep(0.1 * attempts)
            finally:
                conn.close()

    def _claim_one(self, queue_name: str, lease_seconds: int) -> Optional[dict]:
        use_priority_order = queue_name == "transcript_check"
        order_by = "available_at ASC, id ASC"
        if use_priority_order:
            order_by = """
                    CASE
                        WHEN json_valid(payload_json)
                        THEN COALESCE(CAST(json_extract(payload_json, '$.priority') AS INTEGER), 0)
                        ELSE 0
                    END DESC,
                    available_at ASC,
                    id ASC
            """

        conn = self._get_connection(timeout=2.0)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE queue_messages
                SET status = 'pending',
                    locked_until = NULL,
                    worker_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE queue_name = ?
                  AND status = 'claimed'
                  AND locked_until IS NOT NULL
                  AND locked_until < CURRENT_TIMESTAMP
                """,
                (queue_name,),
            )
            try:
                row = conn.execute(
                    f"""
                    SELECT id, payload_json
                    FROM queue_messages
                    WHERE queue_name = ?
                      AND status = 'pending'
                      AND available_at <= CURRENT_TIMESTAMP
                    ORDER BY {order_by}
                    LIMIT 1
                    """,
                    (queue_name,),
                ).fetchone()
            except sqlite3.OperationalError as e:
                if use_priority_order and "json_" in str(e).lower():
                    row = conn.execute(
                        """
                        SELECT id, payload_json
                        FROM queue_messages
                        WHERE queue_name = ?
                          AND status = 'pending'
                          AND available_at <= CURRENT_TIMESTAMP
                        ORDER BY available_at ASC, id ASC
                        LIMIT 1
                        """,
                        (queue_name,),
                    ).fetchone()
                else:
                    raise

            if row is None:
                conn.commit()
                return None

            worker_id = f"{threading.current_thread().name}:{threading.get_ident()}"
            locked_until = datetime.utcnow() + timedelta(seconds=max(lease_seconds, 30))
            claimed = conn.execute(
                """
                UPDATE queue_messages
                SET status = 'claimed',
                    locked_until = ?,
                    worker_id = ?,
                    delivery_attempts = delivery_attempts + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
                """,
                (locked_until, worker_id, row["id"]),
            ).rowcount
            conn.commit()
            if claimed != 1:
                return None

            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError as error:
                self.fail_message(row["id"], f"Invalid queue payload: {error}")
                return None
            if not isinstance(payload, dict):
                self.fail_message(row["id"], "Queue payload must be a JSON object")
                return None
            payload["__queue_message_id"] = row["id"]
            return payload
        finally:
            conn.close()

    def dequeue(
        self,
        queue_name: str,
        timeout: int = 15,
        *,
        lease_seconds: int = 600,
    ) -> Optional[dict]:
        timeout_seconds = max(float(timeout), 0.0)
        try:
            job = self._claim_one(queue_name, lease_seconds)
            if job is not None or timeout_seconds == 0:
                return job
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower():
                logger.exception("Queue claim failed for %s", queue_name)
                raise

        event = self._event_for(queue_name)
        event.wait(timeout_seconds)
        event.clear()
        try:
            return self._claim_one(queue_name, lease_seconds)
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower():
                logger.exception("Queue claim failed for %s", queue_name)
                raise
            return None

    def ack(self, job: dict) -> bool:
        message_id = job.get("__queue_message_id")
        if not message_id:
            return False
        conn = self._get_connection()
        try:
            deleted = conn.execute(
                "DELETE FROM queue_messages WHERE id = ?",
                (message_id,),
            ).rowcount
            conn.commit()
            return deleted == 1
        finally:
            conn.close()

    def release(self, job: dict, *, delay_seconds: int = 0, error: str = "") -> bool:
        message_id = job.get("__queue_message_id")
        if not message_id:
            return False
        available_at = datetime.utcnow() + timedelta(seconds=max(delay_seconds, 0))
        conn = self._get_connection()
        try:
            updated = conn.execute(
                """
                UPDATE queue_messages
                SET status = 'pending',
                    available_at = ?,
                    locked_until = NULL,
                    worker_id = NULL,
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (available_at, (error or "")[:1000], message_id),
            ).rowcount
            conn.commit()
            if updated:
                row = conn.execute(
                    "SELECT queue_name FROM queue_messages WHERE id = ?",
                    (message_id,),
                ).fetchone()
                if row:
                    self.notify(row["queue_name"])
            return updated == 1
        finally:
            conn.close()

    def fail_message(self, message_id: int, error: str) -> None:
        conn = self._get_connection()
        try:
            conn.execute(
                """
                UPDATE queue_messages
                SET status = 'failed',
                    locked_until = NULL,
                    worker_id = NULL,
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ((error or "")[:1000], message_id),
            )
            conn.commit()
        finally:
            conn.close()

    def recover_claims(self) -> int:
        conn = self._get_connection()
        try:
            recovered = conn.execute(
                """
                UPDATE queue_messages
                SET status = 'pending',
                    locked_until = NULL,
                    worker_id = NULL,
                    available_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'claimed'
                """
            ).rowcount
            conn.commit()
            if recovered:
                for queue_name in ("transcript_check", "analysis", "email"):
                    self.notify(queue_name)
            return recovered
        finally:
            conn.close()

    def ping(self) -> bool:
        conn = self._get_connection(timeout=1.0)
        try:
            conn.execute("SELECT 1 FROM queue_messages LIMIT 1")
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def length(self, queue_name: str) -> int:
        conn = self._get_connection(timeout=1.0)
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM queue_messages
                WHERE queue_name = ? AND status IN ('pending', 'claimed')
                """,
                (queue_name,),
            ).fetchone()
            return int(row["count"]) if row else 0
        except Exception:
            return 0
        finally:
            conn.close()
