import threading
import time
from datetime import datetime, timedelta

from config import DATABASE_PATH
from db import get_db_connection
from services.queue_service import QueueService
from services.email_service import EmailService, PermanentEmailError
from services.retry_utils import compute_backoff_seconds
from services.stock_activity_service import StockActivityService

MAX_EMAIL_ATTEMPTS = 6


def _safe_print(message: str):
    """Print that can never raise (Windows pipes may reject Unicode)."""
    try:
        print(message)
    except Exception:
        pass


class EmailQueueWorker:
    def __init__(self):
        self.db_path = str(DATABASE_PATH)
        self.queue = QueueService()
        self.email_service = EmailService()
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
                job = self.queue.dequeue("email", timeout=15, lease_seconds=180)
            except Exception as error:
                _safe_print(f"[EmailWorker] Queue claim failed: {error}")
                time.sleep(5)
                continue
            if not job:
                continue
            outbox_id = job.get("email_outbox_id")
            if not outbox_id:
                self.queue.ack(job)
                continue
            try:
                self._process_job(outbox_id)
                self.queue.ack(job)
            except Exception as e:
                try:
                    self.queue.release(job, delay_seconds=60, error=str(e))
                except Exception as release_error:
                    _safe_print(f"[EmailWorker] Job release failed: {release_error}")
                _safe_print(f"[EmailWorker] Job {outbox_id} failed: {e}")

    def _process_job(self, outbox_id: int):
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, analysis_id, recipient, status, attempts
                FROM email_outbox
                WHERE id = ?
                """,
                (outbox_id,),
            )
            outbox = cursor.fetchone()
            if not outbox:
                return
            if outbox["status"] in {"done", "failed"}:
                return

            cursor.execute(
                """
                UPDATE email_outbox
                SET status = 'in_progress', locked_until = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (datetime.utcnow() + timedelta(minutes=3), outbox_id),
            )
            conn.commit()

            cursor.execute(
                """
                SELECT ta.id as analysis_id, ta.llm_output, ta.model_provider,
                       COALESCE(ta.model_name, lm.model_id, CAST(ta.model_id AS TEXT)) AS model_name,
                       t.stock_id, t.quarter, t.year, t.source_url,
                       s.stock_symbol, s.bse_code, s.isin_number, s.stock_name
                FROM transcript_analyses ta
                JOIN transcripts t ON t.id = ta.transcript_id
                JOIN stocks s ON s.id = t.stock_id
                LEFT JOIN llm_models lm ON lm.id = ta.model_id
                WHERE ta.id = ?
                """,
                (outbox["analysis_id"],),
            )
            analysis = cursor.fetchone()
            if not analysis:
                raise ValueError("Analysis not found for email")

            symbol = (
                analysis["stock_symbol"]
                or analysis["bse_code"]
                or analysis["isin_number"]
            )
            model_name = analysis["model_name"] or analysis["model_provider"]

            self.email_service.send_analysis_email(
                to_email=outbox["recipient"],
                stock_symbol=symbol,
                stock_name=analysis["stock_name"],
                quarter=analysis["quarter"],
                year=analysis["year"],
                analysis_content=analysis["llm_output"],
                model_provider=analysis["model_provider"],
                model_name=model_name,
                transcript_url=analysis["source_url"],
            )

            cursor.execute(
                """
                UPDATE email_outbox
                SET status = 'done', retry_next_at = NULL, locked_until = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (outbox_id,),
            )
            conn.commit()
            self.activity_service.safe_log_event(
                analysis["stock_id"],
                "email",
                "success",
                "Analysis email sent successfully",
                quarter=analysis["quarter"],
                year=analysis["year"],
            )

        except Exception as e:
            cursor.execute("SELECT attempts FROM email_outbox WHERE id = ?", (outbox_id,))
            row = cursor.fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            non_retryable = isinstance(e, PermanentEmailError) or str(e) == "Analysis not found for email"
            exhausted = attempts >= MAX_EMAIL_ATTEMPTS
            status = "failed" if non_retryable or exhausted else "retrying"
            retry_next_at = None
            if status == "retrying":
                retry_next_at = datetime.utcnow() + timedelta(
                    seconds=compute_backoff_seconds(attempts)
                )

            cursor.execute(
                """
                UPDATE email_outbox
                SET status = ?, attempts = ?, retry_next_at = ?,
                    locked_until = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, attempts, retry_next_at, outbox_id),
            )
            conn.commit()
            if 'analysis' in locals() and analysis:
                if non_retryable:
                    failure_reason = "Email delivery needs attention; retrying cannot fix the SMTP configuration"
                elif exhausted:
                    failure_reason = f"Email delivery stopped after {MAX_EMAIL_ATTEMPTS} attempts"
                else:
                    failure_reason = f"Email delivery failed: {str(e)[:700]}"
                self.activity_service.safe_log_event(
                    analysis["stock_id"],
                    "email",
                    "error",
                    failure_reason,
                    quarter=analysis["quarter"],
                    year=analysis["year"],
                    details={
                        "attempt": attempts,
                        "failed": status == "failed",
                        "error": str(e)[:700],
                        "retry_next_at": retry_next_at,
                    },
                )
            _safe_print(f"[EmailWorker] Job {outbox_id} error: {e}")
        finally:
            conn.close()
