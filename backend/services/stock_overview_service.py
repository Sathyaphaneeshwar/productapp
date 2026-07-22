from datetime import datetime, timezone
from typing import Any, Optional

from config import DATABASE_PATH
from db import get_db_connection


class StockOverviewService:
    """Build one stock-scoped view from the app's existing operational data."""

    def __init__(self, db_path: str = None):
        self.db_path = str(db_path or DATABASE_PATH)

    def get_db_connection(self):
        return get_db_connection(self.db_path)

    @staticmethod
    def _to_utc_iso(value: Any) -> Optional[str]:
        if value is None:
            return None
        raw = str(value).strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace(" ", "T").replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            return raw

    def _normalize_rows(self, rows, timestamp_fields=()):
        normalized = []
        for row in rows:
            item = dict(row)
            for field in timestamp_fields:
                if field in item:
                    item[field] = self._to_utc_iso(item[field])
            normalized.append(item)
        return normalized

    @staticmethod
    def _table_exists(conn, table_name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone() is not None

    def get_overview(self, stock_id: int) -> Optional[dict[str, Any]]:
        conn = self.get_db_connection()
        try:
            stock_row = conn.execute(
                """
                SELECT s.id,
                       COALESCE(s.stock_symbol, s.bse_code, s.isin_number) AS symbol,
                       s.stock_symbol,
                       s.bse_code,
                       s.isin_number,
                       s.stock_name AS name,
                       s.source,
                       s.is_active,
                       s.created_at,
                       s.updated_at,
                       w.added_at AS watchlist_added_at,
                       CASE WHEN w.stock_id IS NULL THEN 0 ELSE 1 END AS in_watchlist
                FROM stocks s
                LEFT JOIN watchlist_items w ON w.stock_id = s.id
                WHERE s.id = ?
                """,
                (stock_id,),
            ).fetchone()
            if not stock_row:
                return None

            stock = dict(stock_row)
            for field in ("created_at", "updated_at", "watchlist_added_at"):
                stock[field] = self._to_utc_iso(stock.get(field))
            stock["is_active"] = bool(stock["is_active"])
            stock["in_watchlist"] = bool(stock["in_watchlist"])

            groups = self._normalize_rows(
                conn.execute(
                    """
                    SELECT g.id, g.name, g.is_active, gs.added_at,
                           COUNT(gr.id) AS deep_research_count,
                           SUM(CASE WHEN gr.status = 'done' THEN 1 ELSE 0 END) AS deep_research_done_count,
                           MAX(gr.updated_at) AS latest_research_at
                    FROM group_stocks gs
                    JOIN groups g ON g.id = gs.group_id
                    LEFT JOIN group_research_runs gr ON gr.group_id = g.id
                    WHERE gs.stock_id = ?
                    GROUP BY g.id, g.name, g.is_active, gs.added_at
                    ORDER BY g.is_active DESC, g.name ASC
                    """,
                    (stock_id,),
                ).fetchall(),
                ("added_at", "latest_research_at"),
            )
            for group in groups:
                group["is_active"] = bool(group["is_active"])
                group["deep_research_count"] = int(group["deep_research_count"] or 0)
                group["deep_research_done_count"] = int(group["deep_research_done_count"] or 0)

            transcripts = self._normalize_rows(
                conn.execute(
                    """
                    SELECT t.id, t.quarter, t.year, t.status, t.event_date,
                           t.source_url, t.analysis_status, t.analysis_error,
                           t.created_at, t.updated_at,
                           COUNT(ta.id) AS analysis_count
                    FROM transcripts t
                    LEFT JOIN transcript_analyses ta ON ta.transcript_id = t.id
                    WHERE t.stock_id = ?
                    GROUP BY t.id
                    ORDER BY t.year DESC,
                             CASE t.quarter WHEN 'Q4' THEN 4 WHEN 'Q3' THEN 3 WHEN 'Q2' THEN 2 ELSE 1 END DESC,
                             t.updated_at DESC
                    LIMIT 24
                    """,
                    (stock_id,),
                ).fetchall(),
                ("event_date", "created_at", "updated_at"),
            )

            analyses = self._normalize_rows(
                conn.execute(
                    """
                    SELECT ta.id, ta.transcript_id, ta.llm_output, ta.created_at,
                           ta.model_provider,
                           COALESCE(ta.model_name, lm.model_id, CAST(ta.model_id AS TEXT)) AS model_name,
                           ta.tokens_used_input, ta.tokens_used_output, ta.cost_usd,
                           t.quarter, t.year
                    FROM transcript_analyses ta
                    JOIN transcripts t ON t.id = ta.transcript_id
                    LEFT JOIN llm_models lm ON lm.id = ta.model_id
                    WHERE t.stock_id = ?
                    ORDER BY ta.created_at DESC
                    LIMIT 20
                    """,
                    (stock_id,),
                ).fetchall(),
                ("created_at",),
            )

            analysis_jobs = self._normalize_rows(
                conn.execute(
                    """
                    SELECT aj.id, aj.transcript_id, aj.status, aj.attempts, aj.force,
                           aj.retry_next_at, aj.locked_until, aj.created_at, aj.updated_at,
                           t.quarter, t.year, t.analysis_error AS error_message
                    FROM analysis_jobs aj
                    JOIN transcripts t ON t.id = aj.transcript_id
                    WHERE t.stock_id = ?
                    ORDER BY aj.updated_at DESC, aj.id DESC
                    LIMIT 20
                    """,
                    (stock_id,),
                ).fetchall(),
                ("retry_next_at", "locked_until", "created_at", "updated_at"),
            )

            group_research = self._normalize_rows(
                conn.execute(
                    """
                    SELECT gr.id, gr.group_id, g.name AS group_name, g.is_active AS group_is_active,
                           gr.quarter, gr.year, gr.status, gr.model_provider, gr.model_id,
                           gr.error_message, gr.llm_output,
                           gr.created_at, gr.updated_at
                    FROM group_research_runs gr
                    JOIN groups g ON g.id = gr.group_id
                    JOIN group_stocks gs ON gs.group_id = gr.group_id
                    WHERE gs.stock_id = ?
                    ORDER BY gr.updated_at DESC, gr.id DESC
                    LIMIT 24
                    """,
                    (stock_id,),
                ).fetchall(),
                ("created_at", "updated_at"),
            )

            document_research = []
            if self._table_exists(conn, "document_research_runs"):
                document_research = self._normalize_rows(
                    conn.execute(
                        """
                        SELECT id, document_years, document_type, status,
                               model_provider, model_id, error_message, llm_output,
                               created_at, updated_at
                        FROM document_research_runs
                        WHERE stock_id = ?
                        ORDER BY updated_at DESC, id DESC
                        LIMIT 20
                        """,
                        (stock_id,),
                    ).fetchall(),
                    ("created_at", "updated_at"),
                )

            fetch_schedule_row = conn.execute(
                """
                SELECT quarter, year, priority, next_check_at, last_status,
                       last_checked_at, last_available_at, attempts, locked_until, updated_at
                FROM transcript_fetch_schedule
                WHERE stock_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (stock_id,),
            ).fetchone()
            fetch_schedule = dict(fetch_schedule_row) if fetch_schedule_row else None
            if fetch_schedule:
                for field in ("next_check_at", "last_checked_at", "last_available_at", "locked_until", "updated_at"):
                    fetch_schedule[field] = self._to_utc_iso(fetch_schedule.get(field))

            latest_email_row = conn.execute(
                """
                SELECT eo.id, eo.recipient, eo.status, eo.attempts, eo.scheduled_at,
                       eo.retry_next_at, eo.locked_until, eo.updated_at
                FROM email_outbox eo
                JOIN transcript_analyses ta ON ta.id = eo.analysis_id
                JOIN transcripts t ON t.id = ta.transcript_id
                WHERE t.stock_id = ?
                ORDER BY eo.updated_at DESC, eo.id DESC
                LIMIT 1
                """,
                (stock_id,),
            ).fetchone()
            latest_email = dict(latest_email_row) if latest_email_row else None
            if latest_email:
                for field in ("scheduled_at", "retry_next_at", "locked_until", "updated_at"):
                    latest_email[field] = self._to_utc_iso(latest_email.get(field))

            group_research_counts = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN gr.status = 'done' THEN 1 ELSE 0 END) AS done
                FROM group_research_runs gr
                JOIN group_stocks gs ON gs.group_id = gr.group_id
                WHERE gs.stock_id = ?
                """,
                (stock_id,),
            ).fetchone()
            document_research_counts = {"total": 0, "done": 0}
            if self._table_exists(conn, "document_research_runs"):
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS total,
                           SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done
                    FROM document_research_runs
                    WHERE stock_id = ?
                    """,
                    (stock_id,),
                ).fetchone()
                document_research_counts = dict(row)

            counts = {
                "groups": len(groups),
                "active_groups": sum(1 for group in groups if group["is_active"]),
                "transcripts": conn.execute(
                    "SELECT COUNT(*) FROM transcripts WHERE stock_id = ?", (stock_id,)
                ).fetchone()[0],
                "analyses": conn.execute(
                    """
                    SELECT COUNT(*) FROM transcript_analyses ta
                    JOIN transcripts t ON t.id = ta.transcript_id
                    WHERE t.stock_id = ?
                    """,
                    (stock_id,),
                ).fetchone()[0],
                "deep_research_runs": int(group_research_counts["total"] or 0),
                "deep_research_done": int(group_research_counts["done"] or 0),
                "document_research_runs": int(document_research_counts["total"] or 0),
                "document_research_done": int(document_research_counts["done"] or 0),
                "activity_errors": conn.execute(
                    "SELECT COUNT(*) FROM stock_activity_logs WHERE stock_id = ? AND level = 'error'",
                    (stock_id,),
                ).fetchone()[0],
            }

            return {
                "stock": stock,
                "counts": counts,
                "groups": groups,
                "transcripts": transcripts,
                "analyses": analyses,
                "analysis_jobs": analysis_jobs,
                "group_research": group_research,
                "document_research": document_research,
                "pipeline": {
                    "fetch_schedule": fetch_schedule,
                    "latest_transcript": transcripts[0] if transcripts else None,
                    "latest_analysis_job": analysis_jobs[0] if analysis_jobs else None,
                    "latest_email": latest_email,
                    "latest_group_research": group_research[0] if group_research else None,
                    "latest_document_research": document_research[0] if document_research else None,
                },
            }
        finally:
            conn.close()
