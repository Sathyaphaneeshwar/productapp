import threading
import time
from datetime import datetime, timedelta

from config import DATABASE_PATH
from db import get_db_connection
from services.queue_service import QueueService
from services.prompt_service import PromptService
from services.transcript_service import TranscriptService
from services.llm.llm_service import LLMService
from services.email_outbox_service import EmailOutboxService
from services.retry_utils import compute_backoff_seconds
from services.stock_activity_service import StockActivityService

MAX_ANALYSIS_ATTEMPTS = 5


def _safe_print(message: str):
    """Print that can never raise (Windows pipes may reject Unicode)."""
    try:
        print(message)
    except Exception:
        pass


class AnalysisQueueWorker:
    def __init__(self):
        self.db_path = str(DATABASE_PATH)
        self.queue = QueueService()
        self.prompt_service = PromptService()
        self.transcript_service = TranscriptService()
        self.llm_service = LLMService()
        self.email_outbox_service = EmailOutboxService()
        self.activity_service = StockActivityService()
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
                job = self.queue.dequeue("analysis", timeout=15, lease_seconds=600)
            except Exception as error:
                _safe_print(f"[AnalysisWorker] Queue claim failed: {error}")
                time.sleep(5)
                continue
            if not job:
                continue
            job_id = job.get("analysis_job_id")
            if not job_id:
                self.queue.ack(job)
                continue
            try:
                self._process_job(job_id)
                self.queue.ack(job)
            except Exception as e:
                try:
                    self.queue.release(job, delay_seconds=60, error=str(e))
                except Exception as release_error:
                    _safe_print(f"[AnalysisWorker] Job release failed: {release_error}")
                _safe_print(f"[AnalysisWorker] Job {job_id} failed: {e}")

    def _process_job(self, job_id: int):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, transcript_id, status, attempts, force
                FROM analysis_jobs
                WHERE id = ?
                """,
                (job_id,),
            )
            job = cursor.fetchone()
            if not job:
                return
            if job["status"] in {"done", "failed"}:
                return

            cursor.execute(
                """
                UPDATE analysis_jobs
                SET status = 'in_progress', locked_until = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (datetime.utcnow() + timedelta(hours=2), job_id),
            )
            conn.commit()

            transcript_id = job["transcript_id"]
            force = bool(job["force"])

            cursor.execute(
                """
                SELECT t.id, t.stock_id, t.quarter, t.year, t.source_url, t.status
                FROM transcripts t
                WHERE t.id = ?
                """,
                (transcript_id,),
            )
            transcript = cursor.fetchone()
            if not transcript:
                raise ValueError("Transcript not found")
            if transcript["status"] != "available" or not transcript["source_url"]:
                raise ValueError("Transcript not available for analysis")

            stock_id = transcript["stock_id"]
            cursor.execute("SELECT stock_symbol, bse_code, stock_name FROM stocks WHERE id = ?", (stock_id,))
            stock = cursor.fetchone()
            if not stock:
                raise ValueError("Stock not found")

            # Stock-level analysis is only for watchlist stocks.
            cursor.execute("SELECT 1 FROM watchlist_items WHERE stock_id = ? LIMIT 1", (stock_id,))
            if cursor.fetchone() is None:
                raise ValueError("Stock not in watchlist")

            cursor.execute(
                """
                UPDATE transcripts
                SET analysis_status = 'in_progress',
                    analysis_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (transcript_id,),
            )
            conn.commit()

            transcript_text = self.transcript_service.download_and_extract(transcript["source_url"])

            system_prompt = self.prompt_service.resolve_prompt(stock_id)
            llm_response = self.llm_service.generate(
                prompt=f"Here is the transcript text:\n\n{transcript_text}",
                system_prompt=system_prompt,
                thinking_mode=True,
                max_tokens=12000,
                task_type="watchlist",
            )
            llm_output = llm_response.content
            provider_name = llm_response.provider_name
            model_name = getattr(llm_response, "model_id", None)
            database_model_id = getattr(llm_response, "database_model_id", None)

            cursor.execute(
                """
                INSERT INTO transcript_analyses (
                    transcript_id, prompt_snapshot, llm_output,
                    model_provider, model_id, model_name
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    transcript_id,
                    system_prompt,
                    llm_output,
                    provider_name,
                    database_model_id,
                    model_name,
                ),
            )
            new_analysis_id = cursor.lastrowid

            if force:
                cursor.execute(
                    """
                    DELETE FROM transcript_analyses
                    WHERE transcript_id = ? AND id != ?
                    """,
                    (transcript_id, new_analysis_id),
                )
            conn.commit()

            cursor.execute(
                """
                UPDATE transcripts
                SET analysis_status = 'done',
                    analysis_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (transcript_id,),
            )

            cursor.execute(
                """
                UPDATE analysis_jobs
                SET status = 'done', retry_next_at = NULL, locked_until = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )
            conn.commit()

            self.activity_service.safe_log_event(
                stock_id,
                "analysis",
                "success",
                "Stock analysis completed",
                quarter=transcript["quarter"],
                year=transcript["year"],
                details={
                    "provider": provider_name,
                    "model": model_name,
                },
            )

            try:
                email_jobs = self.email_outbox_service.enqueue_for_analysis(new_analysis_id)
                if email_jobs == 0:
                    self.activity_service.safe_log_event(
                        stock_id,
                        "email",
                        "error",
                        "No active email recipients; analysis email was not queued",
                        quarter=transcript["quarter"],
                        year=transcript["year"],
                    )
            except Exception as email_error:
                self.activity_service.safe_log_event(
                    stock_id,
                    "email",
                    "error",
                    f"Could not queue analysis email: {str(email_error)[:700]}",
                    quarter=transcript["quarter"],
                    year=transcript["year"],
                )
                _safe_print(f"[AnalysisWorker] Analysis {new_analysis_id} email enqueue failed: {email_error}")

        except Exception as e:
            message = str(e)
            non_retryable = message in {
                "Stock not in watchlist",
                "Transcript not available for analysis",
                "Transcript not found",
                "Stock not found",
            }

            cursor.execute("SELECT attempts FROM analysis_jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            exhausted = attempts >= MAX_ANALYSIS_ATTEMPTS
            retry_next_at = None
            if not non_retryable and not exhausted:
                retry_next_at = datetime.utcnow() + timedelta(
                    seconds=compute_backoff_seconds(attempts)
                )
            status = "failed" if non_retryable or exhausted else "retrying"

            cursor.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, attempts = ?, retry_next_at = ?, locked_until = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, attempts, retry_next_at, job_id),
            )
            if 'transcript_id' in locals():
                cursor.execute(
                    """
                    UPDATE transcripts
                    SET analysis_status = 'error',
                        analysis_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (message[:500], transcript_id),
                )
            conn.commit()
            if 'stock_id' in locals() and 'transcript' in locals():
                self.activity_service.safe_log_event(
                    stock_id,
                    "analysis",
                    "error",
                    f"Stock analysis failed: {message[:700]}",
                    quarter=transcript["quarter"],
                    year=transcript["year"],
                    details={
                        "attempt": attempts,
                        "failed": status == "failed",
                        "will_retry": status == "retrying",
                        "retry_next_at": retry_next_at,
                    },
                )
            _safe_print(f"[AnalysisWorker] Job {job_id} error: {e}")
        finally:
            conn.close()
