from datetime import datetime, timedelta
from typing import Dict, List

from config import DATABASE_PATH
from db import get_db_connection
from services.analysis_job_service import AnalysisJobService
from services.queue_service import QueueService

DEFAULT_STALE_ANALYSIS_MINUTES = 5
DEFAULT_STALE_GROUP_RESEARCH_MINUTES = 180


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
        self.queue = QueueService(self.db_path)

    def get_db_connection(self):
        return get_db_connection(self.db_path)

    def run_startup_recovery(
        self,
        analysis_job_service: AnalysisJobService,
        stale_minutes: int = DEFAULT_STALE_ANALYSIS_MINUTES,
        stale_group_minutes: int = DEFAULT_STALE_GROUP_RESEARCH_MINUTES,
    ) -> Dict[str, int]:
        try:
            stale_minutes = int(stale_minutes)
        except (TypeError, ValueError):
            stale_minutes = DEFAULT_STALE_ANALYSIS_MINUTES
        stale_minutes = max(stale_minutes, 1)
        try:
            stale_group_minutes = int(stale_group_minutes)
        except (TypeError, ValueError):
            stale_group_minutes = DEFAULT_STALE_GROUP_RESEARCH_MINUTES
        stale_group_minutes = max(stale_group_minutes, 30)

        summary = {
            "stale_transcripts_reset": 0,
            "analysis_jobs_recovered": 0,
            "email_jobs_recovered": 0,
            "analysis_jobs_requeued": 0,
            "watchlist_schedule_recovered": 0,
            "watchlist_missing_analysis_requeued": 0,
            "group_runs_recovered": 0,
            "queue_claims_recovered": 0,
        }
        stale_transcript_ids: List[int] = []
        missing_watchlist_analysis_ids: List[int] = []

        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cutoff = datetime.utcnow() - timedelta(minutes=stale_minutes)
            group_cutoff = datetime.utcnow() - timedelta(minutes=stale_group_minutes)
            latest_quarter, latest_year = _get_latest_quarter()

            # This is a single-process runtime: no schedule lease can still be
            # owned after restart. Preserve retry cadence/attempts, but clear
            # every stale lease immediately.
            cursor.execute(
                """
                UPDATE transcript_fetch_schedule
                SET locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE locked_until IS NOT NULL
                """,
            )
            summary["watchlist_schedule_recovered"] = cursor.rowcount

            cursor.execute(
                """
                SELECT id
                FROM transcripts
                WHERE analysis_status = 'in_progress'
                """,
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
                WHERE status IN ('in_progress', 'queued')
                """
            )
            summary["analysis_jobs_recovered"] = cursor.rowcount
            cursor.execute(
                """
                UPDATE analysis_jobs
                SET locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('pending', 'retrying')
                  AND locked_until IS NOT NULL
                """
            )

            cursor.execute(
                """
                UPDATE email_outbox
                SET status = 'retrying',
                    retry_next_at = CURRENT_TIMESTAMP,
                    locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('in_progress', 'queued')
                """
            )
            summary["email_jobs_recovered"] = cursor.rowcount
            cursor.execute(
                """
                UPDATE email_outbox
                SET locked_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('pending', 'retrying')
                  AND locked_until IS NOT NULL
                """
            )

            # Group research uses daemon threads. If the app exits mid-run,
            # those rows otherwise remain in_progress forever and block all
            # future automatic regeneration for the same group/quarter.
            cursor.execute(
                """
                UPDATE group_research_runs
                SET status = 'error',
                    error_message = 'Recovered stale in-progress run after application restart',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'in_progress'
                """,
            )
            summary["group_runs_recovered"] = cursor.rowcount

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
                 AND aj.status IN (
                     'pending', 'queued', 'retrying', 'in_progress', 'failed', 'error'
                 )
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

        summary["queue_claims_recovered"] = self.queue.recover_claims()

        for transcript_id in stale_transcript_ids:
            job_id = analysis_job_service.enqueue_for_transcript(transcript_id)
            if job_id is not None:
                summary["analysis_jobs_requeued"] += 1

        for transcript_id in missing_watchlist_analysis_ids:
            job_id = analysis_job_service.enqueue_for_transcript(transcript_id)
            if job_id is not None:
                summary["watchlist_missing_analysis_requeued"] += 1

        return summary
