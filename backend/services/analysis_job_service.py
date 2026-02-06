import logging
import sqlite3
import time
from typing import Optional

from config import DATABASE_PATH

logger = logging.getLogger(__name__)
from db import get_db_connection
from services.queue_service import QueueService


class AnalysisJobService:
    def __init__(self):
        self.db_path = str(DATABASE_PATH)
        self.queue = QueueService()

    def get_db_connection(self):
        return get_db_connection(self.db_path)

    def _build_idempotency_key(self, transcript_id: int, source_url: str, force: bool) -> str:
        base = f"{transcript_id}:{source_url or ''}"
        if force:
            return f"{base}:force:{int(time.time())}"
        return base

    def enqueue_for_transcript(self, transcript_id: int, force: bool = False) -> Optional[int]:
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT source_url FROM transcripts WHERE id = ?", (transcript_id,))
            row = cursor.fetchone()
            if not row:
                return None
            source_url = row["source_url"]

            if not force:
                cursor.execute(
                    "SELECT 1 FROM transcript_analyses WHERE transcript_id = ? LIMIT 1",
                    (transcript_id,),
                )
                if cursor.fetchone():
                    return None
            idempotency_key = self._build_idempotency_key(transcript_id, source_url, force)

            job_id = None
            try:
                cursor.execute(
                    """
                    INSERT INTO analysis_jobs (transcript_id, status, attempts, idempotency_key, force, created_at, updated_at)
                    VALUES (?, 'pending', 0, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (transcript_id, idempotency_key, int(force)),
                )
                conn.commit()
                job_id = cursor.lastrowid
            except sqlite3.IntegrityError:
                cursor.execute("SELECT id, status FROM analysis_jobs WHERE idempotency_key = ?", (idempotency_key,))
                existing = cursor.fetchone()
                if existing:
                    job_id = existing["id"]
                    if existing["status"] == "done" and not force:
                        return job_id

            if job_id:
                cursor.execute(
                    """
                    UPDATE analysis_jobs
                    SET status = 'queued', locked_until = DATETIME(CURRENT_TIMESTAMP, '+15 minutes'), updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status IN ('pending', 'retrying', 'error')
                    """,
                    (job_id,),
                )
                conn.commit()
                try:
                    self.queue.enqueue("analysis", {"analysis_job_id": job_id})
                except Exception as e:
                    # Fallback: allow scheduler to pick it up quickly if direct enqueue fails.
                    cursor.execute(
                        """
                        UPDATE analysis_jobs
                        SET status = 'pending',
                            retry_next_at = CURRENT_TIMESTAMP,
                            locked_until = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND status = 'queued'
                        """,
                        (job_id,),
                    )
                    conn.commit()
                    logger.warning("Direct queue enqueue failed for analysis job %s: %s", job_id, e)
            return job_id
        finally:
            conn.close()

    def enqueue_job(self, job_id: int) -> None:
        self.queue.enqueue("analysis", {"analysis_job_id": job_id})
