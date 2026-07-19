import json
from datetime import datetime, timezone
from typing import Any, Optional

from config import DATABASE_PATH
from db import get_db_connection


class StockActivityService:
    """Durable, stock-scoped operational history for the polling pipeline."""

    VALID_LEVELS = {"info", "success", "error"}

    def __init__(self, db_path: str = None):
        self.db_path = str(db_path or DATABASE_PATH)

    def get_db_connection(self):
        return get_db_connection(self.db_path)

    def log_event(
        self,
        stock_id: int,
        stage: str,
        level: str,
        message: str,
        *,
        quarter: Optional[str] = None,
        year: Optional[int] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> Optional[int]:
        if not stock_id:
            return None
        normalized_level = level if level in self.VALID_LEVELS else "info"
        conn = self.get_db_connection()
        try:
            cursor = conn.execute(
                """
                INSERT INTO stock_activity_logs (
                    stock_id, stage, level, message, quarter, year, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    int(stock_id),
                    str(stage or "system")[:50],
                    normalized_level,
                    str(message or "")[:1000],
                    quarter,
                    int(year) if year is not None else None,
                    json.dumps(details, default=str) if details else None,
                ),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def safe_log_event(self, *args, **kwargs) -> Optional[int]:
        """Record observability without allowing logging failures to stop a job."""
        try:
            return self.log_event(*args, **kwargs)
        except Exception as error:
            print(f"[StockActivity] Failed to record event: {error}")
            return None

    def get_activity(
        self,
        *,
        stock_id: Optional[int] = None,
        level: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        if level and level not in self.VALID_LEVELS:
            raise ValueError("level must be one of: info, success, error")

        stock_filter = "WHERE stock_id = ?" if stock_id is not None else ""
        params: list[Any] = [int(stock_id)] if stock_id is not None else []
        conn = self.get_db_connection()
        try:
            rows = conn.execute(
                f"""
                WITH activity AS (
                    SELECT
                        'activity:' || sal.id AS event_id,
                        sal.stock_id,
                        sal.stage,
                        sal.level,
                        sal.message,
                        sal.quarter,
                        sal.year,
                        sal.details_json,
                        sal.created_at AS event_at,
                        'activity' AS source
                    FROM stock_activity_logs sal

                    UNION ALL

                    SELECT
                        'transcript:' || te.id,
                        te.stock_id,
                        'transcript',
                        CASE WHEN te.status = 'available' THEN 'success' ELSE 'info' END,
                        CASE
                            WHEN te.status = 'available' THEN 'Transcript became available'
                            WHEN te.status = 'upcoming' THEN 'Earnings call is upcoming'
                            ELSE 'Transcript check completed; no transcript found'
                        END,
                        te.quarter,
                        te.year,
                        NULL,
                        te.observed_at,
                        'transcript_events'
                    FROM transcript_events te

                    UNION ALL

                    SELECT
                        'analysis:' || aj.id,
                        t.stock_id,
                        'analysis',
                        CASE
                            WHEN aj.status = 'done' THEN 'success'
                            WHEN aj.status IN ('error', 'retrying') THEN 'error'
                            ELSE 'info'
                        END,
                        CASE
                            WHEN aj.status = 'done' THEN 'Stock analysis completed'
                            WHEN aj.status = 'error' THEN
                                'Stock analysis failed: ' || COALESCE(t.analysis_error, 'unknown error')
                            WHEN aj.status = 'retrying' THEN
                                'Stock analysis failed and will retry'
                            WHEN aj.status = 'in_progress' THEN 'Stock analysis started'
                            ELSE 'Stock analysis queued'
                        END,
                        t.quarter,
                        t.year,
                        json_object(
                            'status', aj.status,
                            'attempts', aj.attempts,
                            'retry_next_at', aj.retry_next_at
                        ),
                        aj.updated_at,
                        'analysis_jobs'
                    FROM analysis_jobs aj
                    JOIN transcripts t ON t.id = aj.transcript_id

                    UNION ALL

                    SELECT
                        'email:' || eo.id,
                        t.stock_id,
                        'email',
                        CASE
                            WHEN eo.status = 'done' THEN 'success'
                            WHEN eo.status IN ('error', 'retrying') THEN 'error'
                            ELSE 'info'
                        END,
                        CASE
                            WHEN eo.status = 'done' THEN 'Analysis email sent successfully'
                            WHEN eo.status = 'retrying' THEN 'Email delivery failed and will retry'
                            WHEN eo.status = 'error' THEN 'Email delivery failed'
                            ELSE 'Analysis email queued'
                        END,
                        t.quarter,
                        t.year,
                        json_object(
                            'status', eo.status,
                            'attempts', eo.attempts,
                            'retry_next_at', eo.retry_next_at
                        ),
                        eo.updated_at,
                        'email_outbox'
                    FROM email_outbox eo
                    JOIN transcript_analyses ta ON ta.id = eo.analysis_id
                    JOIN transcripts t ON t.id = ta.transcript_id
                )
                SELECT
                    activity.*,
                    COALESCE(s.stock_symbol, s.bse_code) AS symbol,
                    s.stock_name AS stock_name
                FROM activity
                JOIN stocks s ON s.id = activity.stock_id
                {stock_filter}
                ORDER BY datetime(event_at) DESC, event_id DESC
                LIMIT ?
                """,
                (*params, limit * 5),
            ).fetchall()
        finally:
            conn.close()

        events: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for row in rows:
            item = dict(row)
            if level and item["level"] != level:
                continue

            # Legacy transcript polling can contain the same observation every
            # five minutes. Keep the newest identical state to make the page useful.
            dedupe_key = (
                item["stock_id"],
                item["stage"],
                item["level"],
                item["message"],
                item["quarter"],
                item["year"],
            )
            if item["source"] != "activity" and dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            details = None
            if item["details_json"]:
                try:
                    details = json.loads(item["details_json"])
                except (TypeError, json.JSONDecodeError):
                    details = {"raw": item["details_json"]}

            events.append(
                {
                    "id": item["event_id"],
                    "stock_id": item["stock_id"],
                    "symbol": item["symbol"],
                    "stock_name": item["stock_name"],
                    "stage": item["stage"],
                    "level": item["level"],
                    "message": item["message"],
                    "quarter": item["quarter"],
                    "year": item["year"],
                    "details": details,
                    "event_at": self._to_utc_iso(item["event_at"]),
                }
            )
            if len(events) >= limit:
                break
        return events

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
                return parsed.isoformat() + "Z"
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            return raw
