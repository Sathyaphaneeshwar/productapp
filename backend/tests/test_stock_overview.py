import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ["PRODUCT_GEMINI_DISABLE_WORKERS"] = "1"

from services.stock_activity_service import StockActivityService
from services.stock_overview_service import StockOverviewService


class StockOverviewTests(unittest.TestCase):
    def setUp(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db_path = Path(path)
        schema_path = Path(__file__).parents[2] / "database" / "schema.sql"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript(schema_path.read_text())
            connection.execute(
                """
                INSERT INTO stocks (stock_symbol, isin_number, stock_name)
                VALUES ('TEST', 'INE000000001', 'Test Limited')
                """
            )
            connection.execute("INSERT INTO watchlist_items (stock_id) VALUES (1)")
            connection.execute("INSERT INTO groups (name, is_active) VALUES ('Core', 1)")
            connection.execute("INSERT INTO group_stocks (group_id, stock_id) VALUES (1, 1)")
            connection.execute(
                """
                INSERT INTO transcripts (
                    stock_id, quarter, year, source_url, status,
                    analysis_status, analysis_error
                ) VALUES (
                    1, 'Q1', 2027, 'https://example.com/transcript.pdf',
                    'available', 'error', 'Invalid provider credential'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO transcript_analyses (
                    transcript_id, llm_output, model_provider, model_name
                ) VALUES (1, 'Stored analysis output', 'test', 'test-model')
                """
            )
            connection.execute(
                """
                INSERT INTO analysis_jobs (
                    transcript_id, status, attempts, idempotency_key
                ) VALUES (1, 'failed', 1, 'test-job')
                """
            )
            connection.execute(
                """
                INSERT INTO transcript_fetch_schedule (
                    stock_id, quarter, year, priority, next_check_at,
                    last_status, last_checked_at
                ) VALUES (
                    1, 'Q1', 2027, 100, datetime('now', '+30 minutes'),
                    'available', CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                INSERT INTO group_research_runs (
                    group_id, quarter, year, status, llm_output
                ) VALUES (1, 'Q1', 2027, 'done', 'Group research output')
                """
            )
            connection.execute(
                """
                INSERT INTO stock_activity_logs (
                    stock_id, stage, level, message, quarter, year
                ) VALUES (1, 'analysis', 'error', 'Invalid provider credential', 'Q1', 2027)
                """
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(f"{self.db_path}{suffix}").unlink()
            except FileNotFoundError:
                pass

    def test_overview_aggregates_membership_analysis_and_research(self):
        overview = StockOverviewService(str(self.db_path)).get_overview(1)

        self.assertEqual(overview["stock"]["symbol"], "TEST")
        self.assertTrue(overview["stock"]["in_watchlist"])
        self.assertEqual(overview["counts"]["groups"], 1)
        self.assertEqual(overview["counts"]["analyses"], 1)
        self.assertEqual(overview["counts"]["deep_research_done"], 1)
        self.assertEqual(overview["groups"][0]["name"], "Core")
        self.assertEqual(overview["analyses"][0]["llm_output"], "Stored analysis output")
        self.assertEqual(overview["pipeline"]["latest_analysis_job"]["status"], "failed")
        self.assertEqual(
            overview["pipeline"]["latest_analysis_job"]["error_message"],
            "Invalid provider credential",
        )
        self.assertIsNotNone(overview["pipeline"]["fetch_schedule"]["next_check_at"])

    def test_failed_job_is_an_error_event_with_details(self):
        events = StockActivityService(str(self.db_path)).get_activity(
            stock_id=1,
            level="error",
        )
        job_event = next(event for event in events if event["id"] == "analysis:1")

        self.assertEqual(job_event["level"], "error")
        self.assertIn("Invalid provider credential", job_event["message"])
        self.assertEqual(job_event["details"]["status"], "failed")
        self.assertEqual(job_event["details"]["attempts"], 1)
        self.assertEqual(job_event["details"]["error"], "Invalid provider credential")


if __name__ == "__main__":
    unittest.main()
