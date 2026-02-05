import sqlite3
from typing import Optional

from config import DATABASE_PATH
from db import get_db_connection
from services.queue_service import QueueService
from services.email_service import EmailService


class EmailOutboxService:
    def __init__(self):
        self.db_path = str(DATABASE_PATH)
        self.queue = QueueService()
        self.email_service = EmailService()

    def get_db_connection(self):
        return get_db_connection(self.db_path)

    def enqueue_for_analysis(self, analysis_id: int) -> int:
        recipients = self.email_service.get_active_email_list()
        if not recipients:
            return 0

        conn = self.get_db_connection()
        cursor = conn.cursor()
        created = 0
        try:
            for recipient in recipients:
                try:
                    cursor.execute(
                        """
                        INSERT INTO email_outbox (analysis_id, recipient, status, attempts, scheduled_at, locked_until, created_at, updated_at)
                        VALUES (?, ?, 'queued', 0, CURRENT_TIMESTAMP, DATETIME(CURRENT_TIMESTAMP, '+15 minutes'), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """,
                        (analysis_id, recipient),
                    )
                    conn.commit()
                    outbox_id = cursor.lastrowid
                    self.queue.enqueue("email", {"email_outbox_id": outbox_id})
                    created += 1
                except sqlite3.IntegrityError:
                    # Already exists, skip
                    continue
        finally:
            conn.close()
        return created

    def enqueue_job(self, outbox_id: int) -> None:
        self.queue.enqueue("email", {"email_outbox_id": outbox_id})
