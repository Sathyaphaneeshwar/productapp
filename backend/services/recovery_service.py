from datetime import datetime, timedelta
from typing import Dict, List

from config import DATABASE_PATH
from db import get_db_connection
from services.analysis_job_service import AnalysisJobService

DEFAULT_STALE_ANALYSIS_MINUTES = 5


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
            "watchlist_schedule_recovered": 0,
            "watchlist_missing_analysis_requeued": 0,
        }
        stale_transcript_ids: List[int] = []
        missing_watchlist_analysis_ids: List[int] = []

        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cutoff = datetime.now() - timedelta(minutes=stale_minutes)
            latest_quarter, latest_year = _get_latest_quarter()

            # Recover watchlist rows that were left in error/backoff states.
            cursor.execute(
                """
                UPDATE transcript_fetch_schedule
                SET next_check_at = CURRENT_TIMESTAMP,
                    attempts = 0,
                    last_status = NULL,
                    last_checked_at = NULL,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE quarter = ? AND year = ?
                  AND stock_id IN (SELECT stock_id FROM watchlist_items)
                  AND (last_status = 'error' OR attempts > 0)
                  AND (
                        EXISTS (
                            SELECT 1
                            FROM transcripts t
                            WHERE t.stock_id = transcript_fetch_schedule.stock_id
                              AND t.quarter = transcript_fetch_schedule.quarter
                              AND t.year = transcript_fetch_schedule.year
                              AND t.status != 'available'
                        )
                        OR NOT EXISTS (
                            SELECT 1
                            FROM transcripts t
                            WHERE t.stock_id = transcript_fetch_schedule.stock_id
                              AND t.quarter = transcript_fetch_schedule.quarter
                              AND t.year = transcript_fetch_schedule.year
                        )
                  )
                """,
                (latest_quarter, latest_year),
            )
            summary["watchlist_schedule_recovered"] = cursor.rowcount

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

            # If transcript became available but no analysis was created due to
            # transient failures, requeue it automatically for watchlist stocks.
            cursor.execute(
                """
                SELECT t.id AS transcript_id
                FROM transcripts t
                JOIN watchlist_items w ON w.stock_id = t.stock_id
                LEFT JOIN transcript_analyses ta ON ta.transcript_id = t.id
                LEFT JOIN analysis_jobs aj
                  ON aj.transcript_id = t.id
                 AND aj.status IN ('pending', 'queued', 'retrying', 'in_progress')
                WHERE t.quarter = ? AND t.year = ?
                  AND t.status = 'available'
                  AND t.source_url IS NOT NULL
                GROUP BY t.id
                HAVING COUNT(ta.id) = 0 AND COUNT(aj.id) = 0
                """,
                (latest_quarter, latest_year),
            )
            missing_watchlist_analysis_ids = [row["transcript_id"] for row in cursor.fetchall()]

            conn.commit()
        finally:
            conn.close()

        for transcript_id in stale_transcript_ids:
            job_id = analysis_job_service.enqueue_for_transcript(transcript_id)
            if job_id is not None:
                summary["analysis_jobs_requeued"] += 1

        for transcript_id in missing_watchlist_analysis_ids:
            job_id = analysis_job_service.enqueue_for_transcript(transcript_id)
            if job_id is not None:
                summary["watchlist_missing_analysis_requeued"] += 1

        return summary
