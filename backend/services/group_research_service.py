import threading
import sqlite3
import os
import sys
import html
from datetime import datetime
from typing import List, Tuple, Dict
import markdown
import re

# Add parent directory to path
from config import DATABASE_PATH
from services.transcript_service import TranscriptService
from services.llm.llm_service import LLMService
from services.email_service import EmailService
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), '..', 'templates')


class GroupResearchService:
    """
    Handles per-group deep research runs once all stocks in a group
    have an available transcript for the same quarter.
    """

    def __init__(self):
        self.db_path = str(DATABASE_PATH)
        self.transcript_service = TranscriptService()
        self.llm_service = LLMService()
        self.email_service = EmailService()
        self.ensure_table()

    def get_db_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_table(self):
        """Ensure the group_research_runs table exists (idempotent)."""
        conn = self.get_db_connection()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS group_research_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    quarter TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    prompt_snapshot TEXT,
                    llm_output TEXT,
                    model_provider TEXT,
                    model_id TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(group_id, quarter, year),
                    FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_group_runs_group ON group_research_runs(group_id);
                CREATE INDEX IF NOT EXISTS idx_group_runs_status ON group_research_runs(status);
            """)
            conn.commit()
        finally:
            conn.close()

    def _render_article_html(self, run: Dict, stocks: List[Dict]) -> str:
        """Render a simple HTML view for the group article, similar to stock emails."""
        template_path = os.path.join(TEMPLATE_DIR, 'group_research_article.html')
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                template = f.read()
        except FileNotFoundError:
            # Fallback: basic wrapper
            template = """
            <html><body style="font-family:Segoe UI,Arial,sans-serif;">
            <h2>{{GROUP_NAME}} — {{QUARTER}} {{YEAR}}</h2>
            <p><strong>Stocks:</strong> {{STOCK_LIST}}</p>
            <div>{{CONTENT}}</div>
            </body></html>
            """

        def normalize_markdown(text: str) -> str:
            """
            Clean up common LLM formatting so Markdown (especially tables) renders in HTML emails.
            - Remove leading whitespace before pipe-table rows.
            - Insert a blank line before and after table blocks so python-markdown recognizes them.
            """
            cleaned_lines = []
            in_table = False
            for line in text.splitlines():
                stripped = line.lstrip()
                is_table_row = stripped.startswith("|") and stripped.count("|") >= 2

                if is_table_row and not in_table:
                    if cleaned_lines and cleaned_lines[-1].strip():
                        cleaned_lines.append("")
                    in_table = True
                elif not is_table_row and in_table:
                    if cleaned_lines and cleaned_lines[-1].strip():
                        cleaned_lines.append("")
                    in_table = False

                cleaned_lines.append(stripped if is_table_row else line)

            return "\n".join(cleaned_lines)

        cleaned = normalize_markdown(run.get("llm_output") or "")
        # Convert Markdown to HTML with safe extensions; fallback to escaped text
        content_html = ""
        try:
            content_html = markdown.markdown(cleaned, extensions=['extra', 'tables', 'sane_lists', 'nl2br'])
        except Exception:
            content_html = f"<pre>{html.escape(run.get('llm_output') or '')}</pre>"

        stock_list = ", ".join([s.get("symbol") for s in stocks])
        replacements = {
            "{{GROUP_NAME}}": html.escape(run.get("group_name", "")),
            "{{QUARTER}}": html.escape(run.get("quarter", "")),
            "{{YEAR}}": html.escape(str(run.get("year", ""))),
            "{{MODEL_PROVIDER}}": html.escape(run.get("model_provider") or ""),
            "{{MODEL_ID}}": html.escape(run.get("model_id") or ""),
            "{{STOCK_LIST}}": html.escape(stock_list),
            "{{CONTENT}}": content_html,
            "{{GENERATED_DATE}}": html.escape(run.get("updated_at", "")),
        }
        for key, val in replacements.items():
            template = template.replace(key, val)
        return template

    def _group_stock_ids(self, cursor, group_id: int) -> List[int]:
        cursor.execute("SELECT stock_id FROM group_stocks WHERE group_id = ?", (group_id,))
        return [row["stock_id"] for row in cursor.fetchall()]

    def _available_quarters_for_stock(self, cursor, stock_id: int) -> List[Tuple[str, int]]:
        cursor.execute(
            """
            SELECT quarter, year
            FROM transcripts
            WHERE stock_id = ? AND status = 'available'
            """,
            (stock_id,),
        )
        return [(row["quarter"], row["year"]) for row in cursor.fetchall()]

    def _existing_run(self, cursor, group_id: int, quarter: str, year: int) -> bool:
        cursor.execute(
            "SELECT id FROM group_research_runs WHERE group_id = ? AND quarter = ? AND year = ?",
            (group_id, quarter, year),
        )
        return cursor.fetchone() is not None

    def check_and_trigger_runs(self):
        """
        Scan all active groups. If every stock in a group has an available transcript
        for the same quarter/year, trigger a deep research run (one per quarter).
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT id, name FROM groups WHERE is_active = 1")
            groups = cursor.fetchall()

            for group in groups:
                group_id = group["id"]
                stock_ids = self._group_stock_ids(cursor, group_id)
                if not stock_ids:
                    continue

                # Build intersection of available (quarter, year) across all stocks in the group
                intersection: set[Tuple[str, int]] = set()
                for idx, stock_id in enumerate(stock_ids):
                    quarters = set(self._available_quarters_for_stock(cursor, stock_id))
                    if idx == 0:
                        intersection = quarters
                    else:
                        intersection &= quarters
                    if not intersection:
                        break

                if not intersection:
                    continue

                for quarter, year in intersection:
                    if self._existing_run(cursor, group_id, quarter, year):
                        continue

                    # Insert a pending run and start background processing
                    cursor.execute(
                        """
                        INSERT INTO group_research_runs (group_id, quarter, year, status)
                        VALUES (?, ?, ?, 'pending')
                        """,
                        (group_id, quarter, year),
                    )
                    conn.commit()
                    run_id = cursor.lastrowid
                    threading.Thread(
                        target=self._process_run,
                        args=(run_id, group_id, group["name"], quarter, year),
                        daemon=True,
                    ).start()

        except Exception as e:
            print(f"[GroupResearch] Error scanning groups: {e}")
        finally:
            conn.close()

    def _process_run(self, run_id: int, group_id: int, group_name: str, quarter: str, year: int, allow_partial: bool = False):
        conn = self.get_db_connection()
        cursor = conn.cursor()

        def update_status(status: str, error: str = None):
            cursor.execute(
                """
                UPDATE group_research_runs
                SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, error, run_id),
            )
            conn.commit()

        try:
            update_status("in_progress")

            # Fetch group prompt
            cursor.execute(
                "SELECT deep_research_prompt FROM groups WHERE id = ? AND is_active = 1",
                (group_id,),
            )
            row = cursor.fetchone()
            if not row or not row["deep_research_prompt"]:
                update_status("error", "No deep_research_prompt configured for this group")
                return

            system_prompt = row["deep_research_prompt"]

            # Get stocks in the group
            cursor.execute(
                """
                SELECT s.id, COALESCE(s.stock_symbol, s.bse_code) AS symbol, s.stock_name
                FROM stocks s
                JOIN group_stocks gs ON s.id = gs.stock_id
                WHERE gs.group_id = ?
                """,
                (group_id,),
            )
            stocks = [dict(r) for r in cursor.fetchall()]
            if not stocks:
                update_status("error", "Group has no stocks")
                return

            # Collect transcripts for the target quarter/year
            transcripts: List[Dict] = []
            for stock in stocks:
                cursor.execute(
                    """
                    SELECT id, source_url
                    FROM transcripts
                    WHERE stock_id = ? AND quarter = ? AND year = ? AND status = 'available'
                    """,
                    (stock["id"], quarter, year),
                )
                t_row = cursor.fetchone()
                if not t_row:
                    if allow_partial:
                        continue
                    update_status("error", f"Missing transcript for {stock['symbol']} {quarter} {year}")
                    return
                transcripts.append(
                    {
                        "stock": stock,
                        "transcript_id": t_row["id"],
                        "source_url": t_row["source_url"],
                    }
                )

            if not transcripts:
                update_status("error", "No transcripts available for requested quarter/year")
                return

            # Download/extract text for each transcript (truncate to keep context reasonable)
            parts = []
            usable = []
            for item in transcripts:
                text = ""
                try:
                    text = self.transcript_service.download_and_extract(item["source_url"]) if item["source_url"] else ""
                except Exception as e:
                    text = f"Error extracting text: {e}"
                # Skip obviously bad downloads in partial mode
                if allow_partial and (not text or text.lower().startswith("error extracting") or text.lower().startswith("error downloading")):
                    continue
                if not text or text.lower().startswith("error"):
                    update_status("error", f"Transcript fetch failed for {item['stock']['symbol']}")
                    return
                truncated = text[:12000]  # keep prompt size manageable
                stock = item["stock"]
                usable.append(item["stock"]["symbol"])
                parts.append(
                    f"### {stock['symbol']} - {stock['stock_name']} ({quarter} {year})\n\n{truncated}"
                )

            if not parts:
                update_status("error", "No transcripts could be processed")
                return

            combined_context = "\n\n".join(parts)
            user_prompt = (
                f"You are analyzing group '{group_name}' for {quarter} {year}. "
                "Use the context below (all group stock transcripts) to deliver a comparative deep research summary. "
                "Highlight cross-company themes, divergences, risks, and opportunities."
            )

            try:
                llm_response = self.llm_service.generate(
                    prompt=f"{user_prompt}\n\n{combined_context}",
                    system_prompt=system_prompt,
                    thinking_mode=True,
                    max_tokens=12000,
                )
                llm_output = llm_response.content
                provider_name = llm_response.provider_name
                model_id = getattr(llm_response, "model_id", None)
            except Exception as e:
                update_status("error", f"LLM generation failed: {e}")
                return

            # Save results (store output)
            cursor.execute(
                """
                UPDATE group_research_runs
                SET status = 'done',
                    prompt_snapshot = ?,
                    llm_output = ?,
                    model_provider = ?,
                    model_id = ?,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (system_prompt, llm_output, provider_name, model_id, run_id),
            )
            conn.commit()

            # Render HTML once for email/export
            run_payload = {
                "id": run_id,
                "group_id": group_id,
                "group_name": group_name,
                "quarter": quarter,
                "year": year,
                "llm_output": llm_output,
                "model_provider": provider_name,
                "model_id": model_id,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            rendered_html = self._render_article_html(run_payload, stocks)

            # Send email to active list
            emails = self.email_service.get_active_email_list()
            if emails:
                stocks_list = ", ".join([item["stock"]["symbol"] for item in transcripts])
                body = rendered_html.replace("{{STOCK_LIST}}", html.escape(stocks_list))
                for email in emails:
                    try:
                        self.email_service.send_email(
                            to_email=email,
                            subject=f"Group Research: {group_name} - {quarter} {year}",
                            body=body,
                            is_html=True,
                        )
                    except Exception as e:
                        print(f"[GroupResearch] Failed to send email to {email}: {e}")

        except Exception as e:
            update_status("error", f"Unexpected error: {e}")
        finally:
            conn.close()

    def list_runs(self, group_id: int) -> List[Dict]:
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, quarter, year, status, model_provider, model_id, created_at, updated_at
                FROM group_research_runs
                WHERE group_id = ?
                ORDER BY year DESC, quarter DESC, created_at DESC
                """,
                (group_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_run(self, run_id: int) -> Dict:
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT gr.*, g.name as group_name
                FROM group_research_runs gr
                JOIN groups g ON gr.group_id = g.id
                WHERE gr.id = ?
                """,
                (run_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            run = dict(row)

            # Get stocks for this group (and attach symbols for display)
            cursor.execute(
                """
                SELECT s.id, COALESCE(s.stock_symbol, s.bse_code) AS symbol, s.stock_name
                FROM stocks s
                JOIN group_stocks gs ON s.id = gs.stock_id
                WHERE gs.group_id = ?
                """,
                (run["group_id"],),
            )
            stocks = [dict(r) for r in cursor.fetchall()]

            run["stocks"] = stocks
            run["rendered_html"] = self._render_article_html(run, stocks)
            return run
        finally:
            conn.close()

    def force_run(self, group_id: int, quarter: str, year: int):
        """
        Force-generate a run even if not all stocks have transcripts.
        Stores run with status pending and processes in background.
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            # Upsert run
            cursor.execute(
                """
                INSERT INTO group_research_runs (group_id, quarter, year, status)
                VALUES (?, ?, ?, 'pending')
                ON CONFLICT(group_id, quarter, year) DO UPDATE SET status='pending', updated_at=CURRENT_TIMESTAMP
                WHERE 1=1
                """,
                (group_id, quarter, year),
            )
            conn.commit()

            # Fetch run id
            cursor.execute(
                "SELECT id, (SELECT name FROM groups WHERE id = ?) as group_name FROM group_research_runs WHERE group_id = ? AND quarter = ? AND year = ?",
                (group_id, group_id, quarter, year),
            )
            row = cursor.fetchone()
            if not row:
                return None
            run_id = row["id"]
            group_name = row["group_name"]

            # Mark in_progress and start thread
            threading.Thread(
                target=self._process_run,
                args=(run_id, group_id, group_name, quarter, year, True),
                daemon=True,
            ).start()
            return run_id
        finally:
            conn.close()
