from datetime import datetime, timedelta
from typing import Dict, List

from config import DATABASE_PATH
from db import get_db_connection
from services.analysis_job_service import AnalysisJobService

DEFAULT_STALE_ANALYSIS_MINUTES = 5


class RecoveryService:
    def __init__(self, db_path: str = None):
        self.db_path = str(db_path or DATABASE_PATH)

    def get_db_connection(self):
        return get_db_connection(self.db_path)

    def run_startup_recovery(
        self,
        analysis_job_service: AnalysisJobService,
        stale_minutes: int = DEFAULT_STALE_ANALYSIS_MINUTES,
    ) -> Dict[str, int]:
        try:
            stale_minutes = int(stale_minutes)
        except (TypeError, ValueError):
            stale_minutes = DEFAULT_STALE_ANALYSIS_MINUTES
        stale_minutes = max(stale_minutes, 1)

        summary = {
            "stale_transcripts_reset": 0,
            "analysis_jobs_recovered": 0,
            "email_jobs_recovered": 0,
            "analysis_jobs_requeued": 0,
        }
        stale_transcript_ids: List[int] = []

        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cutoff = datetime.now() - timedelta(minutes=stale_minutes)

            cursor.execute(
                """
                SELECT id
                FROM transcripts
                WHERE analysis_status = 'in_progress'
                  AND COALESCE(updated_at, created_at) < ?
                """,
                (cutoff,),
            )
            stale_transcript_ids = [row["id"] for row in cursor.fetchall()]

            if stale_transcript_ids:
                placeholders = ",".join("?" for _ in stale_transcript_ids)
                cursor.execute(
                    f"""
                    UPDATE transcripts
                    SET analysis_status = NULL,
                        analysis_error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id IN ({placeholders})
                    """,
                    tuple(stale_transcript_ids),
                )
                summary["stale_transcripts_reset"] = cursor.rowcount

            cursor.execute(
                """
                UPDATE analysis_jobs
                SET status = 'retrying',
                    retry_next_at = CURRENT_TIMESTAMP,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'in_progress'
                  AND (locked_until IS NULL OR locked_until < CURRENT_TIMESTAMP)
                """
            )
            summary["analysis_jobs_recovered"] = cursor.rowcount

            cursor.execute(
                """
                UPDATE email_outbox
                SET status = 'retrying',
                    retry_next_at = CURRENT_TIMESTAMP,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'in_progress'
                  AND (locked_until IS NULL OR locked_until < CURRENT_TIMESTAMP)
                """
            )
            summary["email_jobs_recovered"] = cursor.rowcount

            conn.commit()
        finally:
            conn.close()

        for transcript_id in stale_transcript_ids:
            job_id = analysis_job_service.enqueue_for_transcript(transcript_id)
            if job_id is not None:
                summary["analysis_jobs_requeued"] += 1

        return summary
