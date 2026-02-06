import json
import sqlite3
import time
from typing import Optional

from config import DATABASE_PATH


class QueueService:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path or DATABASE_PATH)

    def _get_connection(self, timeout: float = 30.0) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def enqueue(self, queue_name: str, payload: dict) -> None:
        body = json.dumps(payload)
        attempts = 0
        max_attempts = 5
        while True:
            conn = self._get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO queue_messages (queue_name, payload_json, available_at, created_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (queue_name, body),
                )
                conn.commit()
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or attempts >= max_attempts:
                    raise
                attempts += 1
                time.sleep(0.1 * attempts)
            finally:
                conn.close()

    def dequeue(self, queue_name: str, timeout: int = 5) -> Optional[dict]:
        timeout_seconds = max(float(timeout), 0.0)
        deadline = time.monotonic() + timeout_seconds

        while True:
            conn = self._get_connection(timeout=1.0)
            try:
                row = conn.execute(
                    """
                    SELECT id, payload_json
                    FROM queue_messages
                    WHERE queue_name = ? AND available_at <= CURRENT_TIMESTAMP
                    ORDER BY available_at ASC, id ASC
                    LIMIT 1
                    """,
                    (queue_name,),
                ).fetchone()

                if row is not None:
                    deleted = conn.execute(
                        "DELETE FROM queue_messages WHERE id = ?",
                        (row["id"],),
                    ).rowcount
                    conn.commit()
                    if deleted != 1:
                        # Lost race to another worker, keep polling.
                        continue
                    try:
                        return json.loads(row["payload_json"])
                    except json.JSONDecodeError:
                        return None
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower():
                    raise
                # Retry on transient lock errors during concurrent dequeues/writes.
            finally:
                conn.close()

            if timeout_seconds == 0 or time.monotonic() >= deadline:
                return None

            time.sleep(0.1)

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
                "SELECT COUNT(*) AS count FROM queue_messages WHERE queue_name = ?",
                (queue_name,),
            ).fetchone()
            return int(row["count"]) if row else 0
        except Exception:
            return 0
        finally:
            conn.close()
