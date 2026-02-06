import threading
from datetime import datetime, timedelta

from config import DATABASE_PATH
from db import get_db_connection
from services.queue_service import QueueService
from services.prompt_service import PromptService
from services.transcript_service import TranscriptService
from services.llm.llm_service import LLMService
from services.email_outbox_service import EmailOutboxService
from services.retry_utils import compute_backoff_seconds


class AnalysisQueueWorker:
    def __init__(self):
        self.db_path = str(DATABASE_PATH)
        self.queue = QueueService()
        self.prompt_service = PromptService()
        self.transcript_service = TranscriptService()
        self.llm_service = LLMService()
        self.email_outbox_service = EmailOutboxService()
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
            job = self.queue.dequeue("analysis", timeout=5)
            if not job:
                continue
            job_id = job.get("analysis_job_id")
            if not job_id:
                continue
            try:
                self._process_job(job_id)
            except Exception as e:
                self._handle_error(job_id, e)

    def _handle_error(self, job_id: int, error: Exception):
        message = str(error)
        non_retryable = message in {
            "Stock not in watchlist",
            "Stock belongs to active group",
            "Transcript not available for analysis",
            "Transcript not found",
            "Stock not found",
        }
        conn = self.get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT attempts, transcript_id FROM analysis_jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            transcript_id = row["transcript_id"] if row else None
            retry_next_at = None if non_retryable else datetime.now() + timedelta(seconds=compute_backoff_seconds(attempts))
            status = "error" if non_retryable else "retrying"

            cursor.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, attempts = ?, retry_next_at = ?, locked_until = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, attempts, retry_next_at, job_id),
            )
            if transcript_id:
                cursor.execute(
                    """
                    UPDATE transcripts
                    SET analysis_status = 'error',
                        analysis_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND analysis_status = 'in_progress'
                    """,
                    (message[:500], transcript_id),
                )
            conn.commit()
        except Exception as inner_e:
            print(f"[AnalysisWorker] Error handler failed for job {job_id}: {inner_e}")
        finally:
            conn.close()
        print(f"[AnalysisWorker] Job {job_id} error: {error}")

    def _process_job(self, job_id: int):
        # Phase 1: Read job data and acquire lock (short-lived connection)
        conn = self.get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, transcript_id, status, attempts, force FROM analysis_jobs WHERE id = ?",
                (job_id,),
            )
            job = cursor.fetchone()
            if not job:
                return
            if job["status"] == "done":
                return

            cursor.execute(
                """
                UPDATE analysis_jobs
                SET status = 'in_progress', locked_until = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (datetime.now() + timedelta(hours=2), job_id),
            )
            conn.commit()

            transcript_id = job["transcript_id"]
            force = bool(job["force"])
        finally:
            conn.close()

        # Phase 2: Validate and set transcript to in_progress (short-lived connection)
        conn = self.get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, stock_id, quarter, year, source_url, status FROM transcripts WHERE id = ?",
                (transcript_id,),
            )
            transcript = cursor.fetchone()
            if not transcript:
                raise ValueError("Transcript not found")
            if transcript["status"] != "available" or not transcript["source_url"]:
                raise ValueError("Transcript not available for analysis")

            stock_id = transcript["stock_id"]
            source_url = transcript["source_url"]

            cursor.execute("SELECT stock_symbol, bse_code, stock_name FROM stocks WHERE id = ?", (stock_id,))
            stock = cursor.fetchone()
            if not stock:
                raise ValueError("Stock not found")

            cursor.execute("SELECT 1 FROM watchlist_items WHERE stock_id = ? LIMIT 1", (stock_id,))
            if cursor.fetchone() is None:
                raise ValueError("Stock not in watchlist")
            cursor.execute(
                """
                SELECT 1 FROM group_stocks gs JOIN groups g ON g.id = gs.group_id
                WHERE gs.stock_id = ? AND g.is_active = 1 LIMIT 1
                """,
                (stock_id,),
            )
            if cursor.fetchone():
                raise ValueError("Stock belongs to active group")

            cursor.execute(
                """
                UPDATE transcripts
                SET analysis_status = 'in_progress', analysis_error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (transcript_id,),
            )
            conn.commit()
        finally:
            conn.close()

        # Phase 3: Long-running work (no DB connection held)
        transcript_text = self.transcript_service.download_and_extract(source_url)
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
        model_id = getattr(llm_response, "model_id", None)

        # Phase 4: Persist results (short-lived connection)
        conn = self.get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO transcript_analyses (transcript_id, prompt_snapshot, llm_output, model_provider, model_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (transcript_id, system_prompt, llm_output, provider_name, model_id),
            )
            new_analysis_id = cursor.lastrowid

            if force:
                cursor.execute(
                    "DELETE FROM transcript_analyses WHERE transcript_id = ? AND id != ?",
                    (transcript_id, new_analysis_id),
                )

            cursor.execute(
                """
                UPDATE transcripts
                SET analysis_status = 'done', analysis_error = NULL, updated_at = CURRENT_TIMESTAMP
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
        finally:
            conn.close()

        try:
            self.email_outbox_service.enqueue_for_analysis(new_analysis_id)
        except Exception as e:
            print(f"[AnalysisWorker] Email enqueue failed for job {job_id}: {e}")
