#!/usr/bin/env python3
"""Create or sanitize the stock-reference database shipped in installers."""

import argparse
import sqlite3
from pathlib import Path


USER_STATE_TABLES = (
    "stock_import_batches",
    "queue_messages",
    "email_outbox",
    "analysis_jobs",
    "stock_activity_logs",
    "transcript_events",
    "transcript_checks",
    "transcript_fetch_schedule",
    "transcript_analyses",
    "transcripts",
    "group_research_runs",
    "document_research_runs",
    "watchlist_items",
    "group_stocks",
    "groups",
    "email_list",
    "smtp_settings",
    "api_keys",
    "llm_settings",
)


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {
        row[1]
        for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    }


def sanitize_database(database_path: Path) -> None:
    """Remove all user state and reclaim pages so secrets are not recoverable."""
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("BEGIN IMMEDIATE")
        for table_name in USER_STATE_TABLES:
            if _table_exists(connection, table_name):
                connection.execute(f'DELETE FROM "{table_name}"')

        stock_columns = _columns(connection, "stocks")
        if "source" in stock_columns:
            connection.execute(
                "DELETE FROM stocks WHERE COALESCE(source, 'master') != 'master'"
            )

        provider_columns = _columns(connection, "llm_providers")
        secret_columns = provider_columns & {"api_key", "api_key_encrypted"}
        for column_name in secret_columns:
            connection.execute(
                f'UPDATE llm_providers SET "{column_name}" = NULL'
            )
        connection.commit()

        # DELETE alone can leave values in SQLite free pages. VACUUM rewrites the
        # database so credentials and personal data are removed from the file.
        connection.execute("VACUUM")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Sanitized database failed integrity check: {integrity}")
    finally:
        connection.close()


def assert_sanitized(database_path: Path) -> None:
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        for table_name in USER_STATE_TABLES:
            if not _table_exists(connection, table_name):
                continue
            count = connection.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            ).fetchone()[0]
            if count:
                raise RuntimeError(
                    f"Seed database contains {count} row(s) in {table_name}"
                )

        stock_columns = _columns(connection, "stocks")
        if "source" in stock_columns:
            count = connection.execute(
                "SELECT COUNT(*) FROM stocks WHERE COALESCE(source, 'master') != 'master'"
            ).fetchone()[0]
            if count:
                raise RuntimeError(
                    f"Seed database contains {count} user-added stock row(s)"
                )

        provider_columns = _columns(connection, "llm_providers")
        for column_name in provider_columns & {"api_key", "api_key_encrypted"}:
            count = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM llm_providers
                WHERE COALESCE("{column_name}", '') != ''
                """
            ).fetchone()[0]
            if count:
                raise RuntimeError(
                    f"Seed database contains provider credentials in {column_name}"
                )
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "database",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "database" / "stocks.db",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate that the database has no user state without modifying it.",
    )
    args = parser.parse_args()

    if args.check:
        assert_sanitized(args.database)
        print(f"Sanitized seed database verified: {args.database}")
    else:
        sanitize_database(args.database)
        assert_sanitized(args.database)
        print(f"Sanitized seed database written: {args.database}")


if __name__ == "__main__":
    main()
