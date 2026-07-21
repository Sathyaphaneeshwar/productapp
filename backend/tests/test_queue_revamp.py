import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ["PRODUCT_GEMINI_DISABLE_WORKERS"] = "1"

from services.email_queue_worker import EmailQueueWorker
from services.email_service import EmailAuthenticationError, EmailService
from services.queue_scheduler_service import QueueSchedulerService
from services.queue_service import QueueService
from services.recovery_service import RecoveryService
from services.transcript_fetcher_worker import TranscriptFetcherWorker


QUEUE_SCHEMA = """
CREATE TABLE queue_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_name TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'pending',
    locked_until TIMESTAMP,
    worker_id TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    dedupe_key TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX idx_queue_messages_dedupe
ON queue_messages(queue_name, dedupe_key)
WHERE dedupe_key IS NOT NULL;
"""


class TemporaryDatabaseTest(unittest.TestCase):
    def setUp(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db_path = Path(path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(f"{self.db_path}{suffix}").unlink()
            except FileNotFoundError:
                pass

    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


class QueueClaimTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        with self.connect() as connection:
            connection.executescript(QUEUE_SCHEMA)

    def test_message_survives_claim_until_ack_and_dedupes(self):
        queue = QueueService(str(self.db_path))
        self.assertTrue(
            queue.enqueue("analysis", {"analysis_job_id": 7}, dedupe_key="analysis:7")
        )
        self.assertFalse(
            queue.enqueue("analysis", {"analysis_job_id": 7}, dedupe_key="analysis:7")
        )

        job = queue.dequeue("analysis", timeout=0, lease_seconds=60)
        self.assertEqual(job["analysis_job_id"], 7)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM queue_messages"
            ).fetchone()
        self.assertEqual(row["status"], "claimed")

        self.assertEqual(queue.recover_claims(), 1)
        recovered = queue.dequeue("analysis", timeout=0, lease_seconds=60)
        self.assertEqual(recovered["analysis_job_id"], 7)
        self.assertTrue(queue.ack(recovered))
        with self.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM queue_messages"
            ).fetchone()[0]
        self.assertEqual(count, 0)


class SchedulerDedupeTests(TemporaryDatabaseTest):
    class Scheduler(QueueSchedulerService):
        def __init__(self, db_path):
            super().__init__()
            self.test_db_path = str(db_path)
            self.queue = QueueService(self.test_db_path)

        def get_db_connection(self):
            connection = sqlite3.connect(self.test_db_path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection

    def setUp(self):
        super().setUp()
        schema_path = Path(__file__).parents[2] / "database" / "schema.sql"
        with self.connect() as connection:
            connection.executescript(schema_path.read_text())
            connection.execute(
                """
                INSERT INTO stocks (stock_symbol, isin_number, stock_name)
                VALUES ('TEST', 'INE000000001', 'Test Limited')
                """
            )
            connection.execute("INSERT INTO watchlist_items (stock_id) VALUES (1)")

    def test_repeated_check_now_does_not_duplicate_work(self):
        scheduler = self.Scheduler(self.db_path)
        scheduler.trigger_now(fresh=False)
        scheduler.trigger_now(fresh=False)
        with self.connect() as connection:
            count = connection.execute(
                """
                SELECT COUNT(*)
                FROM queue_messages
                WHERE queue_name = 'transcript_check'
                  AND status IN ('pending', 'claimed')
                """
            ).fetchone()[0]
        self.assertEqual(count, 1)


class TranscriptPendingUploadTests(TemporaryDatabaseTest):
    """A recorded concall without a transcript PDF must keep polling fast."""

    def setUp(self):
        super().setUp()
        schema_path = Path(__file__).parents[2] / "database" / "schema.sql"
        with self.connect() as connection:
            connection.executescript(schema_path.read_text())
            connection.execute(
                """
                INSERT INTO stocks (stock_symbol, isin_number, stock_name)
                VALUES ('TEST', 'INE000000001', 'Test Limited')
                """
            )
            connection.execute("INSERT INTO watchlist_items (stock_id) VALUES (1)")
            connection.execute(
                """
                INSERT INTO transcript_fetch_schedule (stock_id, quarter, year, priority, next_check_at)
                VALUES (1, 'Q1', 2027, 100, CURRENT_TIMESTAMP)
                """
            )
            connection.commit()

    def test_recorded_call_without_transcript_is_tracked_as_upcoming(self):
        from services.transcript_service import TranscriptMetadata

        worker = TranscriptFetcherWorker()
        worker.db_path = str(self.db_path)
        worker.activity_service.db_path = str(self.db_path)
        worker.queue = QueueService(str(self.db_path))

        event_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        pending = TranscriptMetadata(
            stock_symbol="TEST",
            quarter="Q1",
            year=2027,
            source_url=None,
            title="Q1 FY2027 Earnings Call (Transcript pending)",
            isin="INE000000001",
            event_date=event_time,
        )

        with patch.object(
            worker.transcript_service, "fetch_concall_states", return_value=([], [pending])
        ), patch.object(
            worker.transcript_service, "get_upcoming_calls", return_value=[]
        ):
            worker._process_job({"stock_id": 1, "quarter": "Q1", "year": 2027})

        with self.connect() as connection:
            transcript = connection.execute(
                "SELECT status, event_date FROM transcripts WHERE stock_id = 1"
            ).fetchone()
            schedule = connection.execute(
                "SELECT last_status, next_check_at FROM transcript_fetch_schedule WHERE stock_id = 1"
            ).fetchone()

        self.assertEqual(transcript["status"], "upcoming")
        self.assertEqual(schedule["last_status"], "upcoming")
        next_check = datetime.fromisoformat(schedule["next_check_at"])
        delay = (next_check - datetime.utcnow()).total_seconds()
        # Post-event cadence: 15 minutes, not the slow "none" backoff.
        self.assertLessEqual(delay, 16 * 60)


class TranscriptCadenceTests(unittest.TestCase):
    def setUp(self):
        self.worker = TranscriptFetcherWorker()

    def assert_delay_close(self, result, expected_seconds, tolerance=5):
        delta = (result - datetime.utcnow()).total_seconds()
        self.assertGreaterEqual(delta, expected_seconds - tolerance)
        self.assertLessEqual(delta, expected_seconds + tolerance)

    def test_event_aware_upcoming_and_error_cadence(self):
        far_event = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        near_event = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        recent_event = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        self.assert_delay_close(
            self.worker._compute_next_check("upcoming", far_event, 0),
            24 * 60 * 60,
        )
        self.assert_delay_close(
            self.worker._compute_next_check("upcoming", near_event, 0),
            30 * 60,
        )
        self.assert_delay_close(
            self.worker._compute_next_check("upcoming", recent_event, 0),
            15 * 60,
        )
        self.assert_delay_close(
            self.worker._compute_next_check("error", None, 1),
            5 * 60,
        )
        self.assert_delay_close(
            self.worker._compute_next_check("error", None, 4),
            6 * 60 * 60,
        )

    def test_none_status_rechecks_fast_for_watchlist_stocks(self):
        # Transcripts often appear within hours of a concall; the previous 12h
        # backoff made the app look broken during earnings season.
        self.assert_delay_close(
            self.worker._compute_next_check("none", None, 0, is_watchlist_stock=True),
            30 * 60,
        )
        self.assert_delay_close(
            self.worker._compute_next_check("none", None, 0, is_watchlist_stock=False),
            2 * 60 * 60,
        )


class EmailSecurityTests(unittest.TestCase):
    class FakeServer:
        def __init__(self, *args, **kwargs):
            self.started_tls = False

        def ehlo(self):
            return None

        def starttls(self, context=None):
            self.started_tls = True

        def login(self, email, password):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def test_port_465_uses_implicit_ssl_and_587_uses_starttls(self):
        service = EmailService()
        config_465 = {
            "smtp_server": "smtp.example.com",
            "smtp_port": 465,
            "smtp_security": "auto",
            "email": "user@example.com",
            "app_password": "secret",
        }
        config_587 = {**config_465, "smtp_port": 587}

        with patch(
            "services.email_service.smtplib.SMTP_SSL",
            side_effect=self.FakeServer,
        ) as ssl_server:
            with service._open_server(config_465):
                pass
            ssl_server.assert_called_once()

        with patch(
            "services.email_service.smtplib.SMTP",
            side_effect=self.FakeServer,
        ) as tls_server:
            with service._open_server(config_587) as server:
                self.assertTrue(server.started_tls)
            tls_server.assert_called_once()


class EmailRetryUtcTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE stocks (
                    id INTEGER PRIMARY KEY,
                    stock_symbol TEXT,
                    bse_code TEXT,
                    isin_number TEXT,
                    stock_name TEXT
                );
                CREATE TABLE transcripts (
                    id INTEGER PRIMARY KEY,
                    stock_id INTEGER,
                    quarter TEXT,
                    year INTEGER,
                    source_url TEXT
                );
                CREATE TABLE llm_models (id INTEGER PRIMARY KEY, model_id TEXT);
                CREATE TABLE transcript_analyses (
                    id INTEGER PRIMARY KEY,
                    transcript_id INTEGER,
                    llm_output TEXT,
                    model_provider TEXT,
                    model_name TEXT,
                    model_id INTEGER
                );
                CREATE TABLE email_outbox (
                    id INTEGER PRIMARY KEY,
                    analysis_id INTEGER,
                    recipient TEXT,
                    status TEXT,
                    attempts INTEGER,
                    retry_next_at TIMESTAMP,
                    locked_until TIMESTAMP,
                    updated_at TIMESTAMP
                );
                INSERT INTO stocks
                VALUES (1, 'TEST', NULL, 'INE000000001', 'Test Limited');
                INSERT INTO transcripts VALUES (1, 1, 'Q1', 2027, 'https://example.com/test.pdf');
                INSERT INTO transcript_analyses
                VALUES (1, 1, 'analysis', 'openai', 'model', NULL);
                INSERT INTO email_outbox
                VALUES (1, 1, 'to@example.com', 'queued', 0, NULL, NULL, CURRENT_TIMESTAMP);
                """
            )

    class ActivitySink:
        def safe_log_event(self, *args, **kwargs):
            return None

    def make_worker(self, error):
        class FailingEmailService:
            def send_analysis_email(self, **kwargs):
                raise error

        worker = EmailQueueWorker()
        worker.db_path = str(self.db_path)
        worker.email_service = FailingEmailService()
        worker.activity_service = self.ActivitySink()
        return worker

    def test_transient_retry_timestamp_is_utc_even_in_ist(self):
        original_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Kolkata"
        if hasattr(time, "tzset"):
            time.tzset()
        try:
            worker = self.make_worker(RuntimeError("temporary SMTP outage"))
            with patch(
                "services.email_queue_worker.compute_backoff_seconds",
                return_value=60,
            ):
                worker._process_job(1)
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT status, retry_next_at FROM email_outbox WHERE id = 1"
                ).fetchone()
            retry_at = datetime.fromisoformat(str(row["retry_next_at"]))
            delta = (retry_at - datetime.utcnow()).total_seconds()
            self.assertEqual(row["status"], "retrying")
            self.assertGreaterEqual(delta, 55)
            self.assertLessEqual(delta, 65)
        finally:
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            if hasattr(time, "tzset"):
                time.tzset()

    def test_authentication_failure_is_terminal(self):
        worker = self.make_worker(EmailAuthenticationError("bad credentials"))
        worker._process_job(1)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status, attempts, retry_next_at FROM email_outbox WHERE id = 1"
            ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempts"], 1)
        self.assertIsNone(row["retry_next_at"])


class StartupRecoveryTests(TemporaryDatabaseTest):
    def setUp(self):
        super().setUp()
        with self.connect() as connection:
            connection.executescript(
                QUEUE_SCHEMA
                + """
                CREATE TABLE stocks (id INTEGER PRIMARY KEY);
                CREATE TABLE watchlist_items (stock_id INTEGER);
                CREATE TABLE transcript_fetch_schedule (
                    id INTEGER PRIMARY KEY,
                    stock_id INTEGER,
                    quarter TEXT,
                    year INTEGER,
                    next_check_at TIMESTAMP,
                    last_status TEXT,
                    last_checked_at TIMESTAMP,
                    attempts INTEGER,
                    locked_until TIMESTAMP,
                    updated_at TIMESTAMP
                );
                CREATE TABLE transcript_checks (
                    stock_id INTEGER PRIMARY KEY,
                    status TEXT,
                    updated_at TIMESTAMP
                );
                CREATE TABLE transcripts (
                    id INTEGER PRIMARY KEY,
                    stock_id INTEGER,
                    quarter TEXT,
                    year INTEGER,
                    status TEXT,
                    source_url TEXT,
                    analysis_status TEXT,
                    analysis_error TEXT,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                );
                CREATE TABLE transcript_analyses (id INTEGER PRIMARY KEY, transcript_id INTEGER);
                CREATE TABLE analysis_jobs (
                    id INTEGER PRIMARY KEY,
                    transcript_id INTEGER,
                    status TEXT,
                    attempts INTEGER,
                    retry_next_at TIMESTAMP,
                    locked_until TIMESTAMP,
                    updated_at TIMESTAMP
                );
                CREATE TABLE email_outbox (
                    id INTEGER PRIMARY KEY,
                    status TEXT,
                    retry_next_at TIMESTAMP,
                    locked_until TIMESTAMP,
                    updated_at TIMESTAMP
                );
                CREATE TABLE group_research_runs (
                    id INTEGER PRIMARY KEY,
                    status TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                );
                INSERT INTO stocks VALUES (1);
                INSERT INTO watchlist_items VALUES (1);
                INSERT INTO transcript_fetch_schedule
                VALUES (1, 1, 'Q1', 2027, CURRENT_TIMESTAMP, NULL, NULL, 0,
                        DATETIME(CURRENT_TIMESTAMP, '+2 hours'), CURRENT_TIMESTAMP);
                INSERT INTO transcripts
                VALUES (1, 1, 'Q1', 2027, 'upcoming', NULL, 'in_progress', NULL,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                INSERT INTO analysis_jobs
                VALUES (1, 1, 'in_progress', 0, NULL,
                        DATETIME(CURRENT_TIMESTAMP, '+2 hours'), CURRENT_TIMESTAMP);
                INSERT INTO email_outbox
                VALUES (1, 'in_progress', NULL,
                        DATETIME(CURRENT_TIMESTAMP, '+1 hour'), CURRENT_TIMESTAMP);
                INSERT INTO group_research_runs
                VALUES (1, 'in_progress', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                INSERT INTO queue_messages (
                    queue_name, payload_json, status, locked_until, created_at, updated_at
                ) VALUES (
                    'analysis', '{"analysis_job_id": 1}', 'claimed',
                    DATETIME(CURRENT_TIMESTAMP, '+10 minutes'),
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                );
                """
            )

    class AnalysisJobs:
        def enqueue_for_transcript(self, transcript_id):
            return None

    def test_startup_clears_all_single_process_locks(self):
        recovery = RecoveryService(str(self.db_path))
        summary = recovery.run_startup_recovery(self.AnalysisJobs())
        with self.connect() as connection:
            analysis = connection.execute(
                "SELECT status, locked_until FROM analysis_jobs WHERE id = 1"
            ).fetchone()
            email = connection.execute(
                "SELECT status, locked_until FROM email_outbox WHERE id = 1"
            ).fetchone()
            schedule_lock = connection.execute(
                "SELECT locked_until FROM transcript_fetch_schedule WHERE id = 1"
            ).fetchone()[0]
            queue_status = connection.execute(
                "SELECT status FROM queue_messages WHERE id = 1"
            ).fetchone()[0]
        self.assertEqual(analysis["status"], "retrying")
        self.assertIsNone(analysis["locked_until"])
        self.assertEqual(email["status"], "retrying")
        self.assertIsNone(email["locked_until"])
        self.assertIsNone(schedule_lock)
        self.assertEqual(queue_status, "pending")
        self.assertEqual(summary["queue_claims_recovered"], 1)


class PackagedSeedPrivacyTests(unittest.TestCase):
    def test_tracked_seed_database_contains_no_user_state_or_credentials(self):
        seed_path = Path(__file__).parents[2] / "database" / "stocks.db"
        connection = sqlite3.connect(seed_path)
        try:
            private_tables = (
                "smtp_settings",
                "email_list",
                "api_keys",
                "watchlist_items",
                "groups",
                "transcripts",
                "analysis_jobs",
                "email_outbox",
                "queue_messages",
            )
            for table_name in private_tables:
                count = connection.execute(
                    f'SELECT COUNT(*) FROM "{table_name}"'
                ).fetchone()[0]
                self.assertEqual(count, 0, table_name)

            provider_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(llm_providers)"
                ).fetchall()
            }
            for column_name in provider_columns & {"api_key", "api_key_encrypted"}:
                count = connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM llm_providers
                    WHERE COALESCE("{column_name}", '') != ''
                    """
                ).fetchone()[0]
                self.assertEqual(count, 0, column_name)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
