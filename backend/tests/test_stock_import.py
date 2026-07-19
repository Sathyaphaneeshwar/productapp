import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ["PRODUCT_GEMINI_DISABLE_WORKERS"] = "1"

import app as backend_app
from services.stock_import_service import (
    StockImportError,
    StockImportService,
    is_valid_isin,
)
from services.transcript_service import TranscriptService


class StockImportTests(unittest.TestCase):
    def setUp(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db_path = Path(path)
        schema_path = Path(__file__).parents[2] / "database" / "schema.sql"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript(schema_path.read_text())
        finally:
            connection.close()
        self.service = StockImportService(self.db_path)

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

    def test_isin_checksum(self):
        self.assertTrue(is_valid_isin("INE009A01021"))
        self.assertFalse(is_valid_isin("INE009A01020"))
        self.assertFalse(is_valid_isin("not-an-isin"))

    def test_csv_requires_isin_company_name_and_supports_bom(self):
        rows = self.service.parse_file(
            "stocks.csv",
            b"\xef\xbb\xbfISIN,CompanyName\r\nINE009A01021,Infosys Limited\r\n",
        )
        valid, invalid, warnings = self.service.validate_rows(rows)
        self.assertEqual(valid[0]["isin"], "INE009A01021")
        self.assertEqual(valid[0]["company_name"], "Infosys Limited")
        self.assertEqual(invalid, [])
        self.assertEqual(warnings, [])

        with self.assertRaises(StockImportError):
            self.service.parse_file(
                "wrong.csv",
                b"isin,name_of_business\nINE009A01021,Infosys Limited\n",
            )

    def test_xlsx_and_duplicate_last_row_wins(self):
        from openpyxl import Workbook

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(["ISIN", "CompanyName"])
        worksheet.append(["INE009A01021", "Old Infosys Name"])
        worksheet.append(["INE009A01021", "Infosys Limited"])
        payload = io.BytesIO()
        workbook.save(payload)
        workbook.close()

        rows = self.service.parse_file("stocks.xlsx", payload.getvalue())
        valid, invalid, warnings = self.service.validate_rows(rows)
        self.assertEqual(invalid, [])
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["company_name"], "Infosys Limited")
        self.assertEqual(len(warnings), 1)

    def test_preview_commit_is_idempotent_and_second_commit_conflicts(self):
        preview = self.service.preview(
            "ipo.csv",
            (
                b"ISIN,CompanyName\n"
                b"INE009A01021,Infosys Limited\n"
                b"INE002A01018,Reliance Industries Limited\n"
            ),
        )
        self.assertEqual(preview["new"], 2)
        result = self.service.commit_batch(preview["batch_id"])
        self.assertEqual(result["new"], 2)

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT isin_number, stock_name, source, stock_symbol
                FROM stocks ORDER BY isin_number
                """
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["source"] == "import" for row in rows))
        self.assertTrue(all(row["stock_symbol"] is None for row in rows))

        with self.assertRaises(StockImportError) as context:
            self.service.commit_batch(preview["batch_id"])
        self.assertEqual(context.exception.status_code, 409)

        second = self.service.preview(
            "ipo.csv",
            b"ISIN,CompanyName\nINE009A01021,Infosys Limited\n",
        )
        self.assertEqual(second["new"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["unchanged"], 1)

    def test_manual_duplicate_returns_existing_record(self):
        created = self.service.create_manual("INE467B01029", "Tata Consultancy Services")
        self.assertEqual(created["source"], "manual")
        with self.assertRaises(StockImportError) as context:
            self.service.create_manual("INE467B01029", "TCS")
        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.details["existing"]["id"], created["id"])

    def test_transcript_service_resolves_an_isin_only_stock(self):
        created = self.service.create_manual("INE758T01015", "Eternal Limited")
        transcript_service = TranscriptService()
        transcript_service.get_db_connection = self.service.get_db_connection
        self.assertEqual(
            transcript_service._get_isin_from_symbol(created["isin"]),
            created["isin"],
        )


class StockImportApiTests(StockImportTests):
    def setUp(self):
        super().setUp()
        self.original_db_path = backend_app.DB_PATH
        backend_app.DB_PATH = str(self.db_path)
        backend_app.app.config["TESTING"] = True
        self.client = backend_app.app.test_client()

    def tearDown(self):
        backend_app.DB_PATH = self.original_db_path
        super().tearDown()

    def test_preview_commit_search_and_reference_safe_delete(self):
        response = self.client.post(
            "/api/stocks/import/preview",
            data={
                "file": (
                    io.BytesIO(
                        b"ISIN,CompanyName\nINE040A01034,HDFC Bank Limited\n"
                    ),
                    "banks.csv",
                )
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        batch_id = response.get_json()["batch_id"]

        response = self.client.post(
            "/api/stocks/import/commit",
            json={"batch_id": batch_id},
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/api/stocks?q=HDFC")
        self.assertEqual(response.status_code, 200)
        stock = response.get_json()[0]
        self.assertEqual(stock["symbol"], "INE040A01034")

        with self.connect() as connection:
            connection.execute(
                "INSERT INTO watchlist_items (stock_id) VALUES (?)",
                (stock["id"],),
            )
            connection.commit()
        response = self.client.delete(f"/api/stocks/{stock['id']}")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["reason"], "in_use")

        response = self.client.put(
            f"/api/stocks/{stock['id']}",
            json={"is_active": False},
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.get("/api/stocks?q=HDFC")
        self.assertEqual(response.get_json(), [])

    def test_template_uses_exact_requested_header(self):
        response = self.client.get("/api/stocks/template.csv")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b"ISIN,CompanyName\r\n"))

    def test_unreferenced_user_stock_can_be_deleted(self):
        response = self.client.post(
            "/api/stocks/manual",
            json={
                "isin": "INE758T01015",
                "company_name": "Eternal Limited",
            },
        )
        self.assertEqual(response.status_code, 201)
        stock_id = response.get_json()["stock"]["id"]
        response = self.client.delete(f"/api/stocks/{stock_id}")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
