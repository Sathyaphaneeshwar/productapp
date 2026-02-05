import threading
from datetime import datetime, timedelta

from config import DATABASE_PATH
from db import get_db_connection
from services.queue_service import QueueService
from services.email_service import EmailService
from services.retry_utils import compute_backoff_seconds


class EmailQueueWorker:
    def __init__(self):
        self.db_path = str(DATABASE_PATH)
        self.queue = QueueService()
        self.email_service = EmailService()
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
            job = self.queue.dequeue("email", timeout=5)
            if not job:
                continue
            outbox_id = job.get("email_outbox_id")
            if not outbox_id:
                continue
            try:
                self._process_job(outbox_id)
            except Exception as e:
                print(f"[EmailWorker] Job {outbox_id} failed: {e}")

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
            if outbox["status"] == "done":
                return

            cursor.execute(
                """
                UPDATE email_outbox
                SET status = 'in_progress', locked_until = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (datetime.now() + timedelta(hours=1), outbox_id),
            )
            conn.commit()

            cursor.execute(
                """
                SELECT ta.id as analysis_id, ta.llm_output, ta.model_provider, ta.model_id,
                       t.stock_id, t.quarter, t.year, t.source_url,
                       s.stock_symbol, s.bse_code, s.stock_name
                FROM transcript_analyses ta
                JOIN transcripts t ON t.id = ta.transcript_id
                JOIN stocks s ON s.id = t.stock_id
                WHERE ta.id = ?
                """,
                (outbox["analysis_id"],),
            )
            analysis = cursor.fetchone()
            if not analysis:
                raise ValueError("Analysis not found for email")

            symbol = analysis["stock_symbol"] or analysis["bse_code"]
            model_name = analysis["model_id"] if analysis["model_id"] else analysis["model_provider"]

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

        except Exception as e:
            cursor.execute("SELECT attempts FROM email_outbox WHERE id = ?", (outbox_id,))
            row = cursor.fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            retry_next_at = datetime.now() + timedelta(seconds=compute_backoff_seconds(attempts))

            cursor.execute(
                """
                UPDATE email_outbox
                SET status = 'retrying', attempts = ?, retry_next_at = ?, locked_until = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (attempts, retry_next_at, outbox_id),
            )
            conn.commit()
            print(f"[EmailWorker] Job {outbox_id} error: {e}")
        finally:
            conn.close()
