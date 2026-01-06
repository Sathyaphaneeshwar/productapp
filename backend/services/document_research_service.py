"""
Document Research Service - Handles annual report fetching and LLM analysis.
"""
import threading
import sqlite3
import os
import sys
import html
import json
import re
import io
import requests
import random
from datetime import datetime
from typing import List, Dict, Optional
import markdown
from bs4 import BeautifulSoup
from io import BytesIO

# Add parent directory to path
from config import DATABASE_PATH
from services.llm.llm_service import LLMService

# PDF generation
try:
    from xhtml2pdf import pisa
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), '..', 'templates')

# Constants from StockLib
MIN_FILE_SIZE = 1024
REQUESTS_CONNECT_TIMEOUT = 15
REQUESTS_READ_TIMEOUT = 300

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36"
]

# Default research prompt
DEFAULT_RESEARCH_PROMPT = """You are a financial analyst reviewing annual reports.

Analyze the provided annual report(s) and create a comprehensive research summary covering:

1. **Business Overview**: Key business segments, revenue breakdown, and market position
2. **Financial Performance**: Revenue growth, profitability trends, key ratios
3. **Management Commentary**: Key insights from management discussion
4. **Risk Factors**: Major risks and challenges identified
5. **Future Outlook**: Growth drivers, expansion plans, guidance if any
6. **Key Metrics**: Important KPIs and how they've trended

Be specific with numbers and percentages. Compare year-over-year where multiple years are provided."""


class DocumentResearchService:
    """
    Handles document research runs - fetches annual reports from screener.in,
    extracts text, and generates LLM analysis.
    """

    def __init__(self):
        self.db_path = str(DATABASE_PATH)
        self.llm_service = LLMService()
        self.ensure_table()

    def get_db_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_table(self):
        """Ensure the document_research_runs table exists (idempotent)."""
        conn = self.get_db_connection()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS document_research_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_id INTEGER NOT NULL,
                    stock_symbol TEXT,
                    stock_name TEXT,
                    document_years TEXT NOT NULL,
                    document_type TEXT DEFAULT 'Annual_Report',
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    llm_output TEXT,
                    model_provider TEXT,
                    model_id TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (stock_id) REFERENCES stocks(id)
                );
                CREATE INDEX IF NOT EXISTS idx_doc_research_stock ON document_research_runs(stock_id);
                CREATE INDEX IF NOT EXISTS idx_doc_research_status ON document_research_runs(status);
            """)
            conn.commit()
        finally:
            conn.close()

    # --- Screener.in Scraping (adapted from StockLib) ---

    def _get_webpage_content(self, stock_symbol: str) -> Optional[str]:
        """Fetch company page from screener.in"""
        url = f"https://www.screener.in/company/{stock_symbol}/consolidated/#documents"
        try:
            headers = {"User-Agent": random.choice(USER_AGENTS)}
            response = requests.get(url, headers=headers, timeout=REQUESTS_CONNECT_TIMEOUT)
            response.raise_for_status()
            return response.text
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise
        except requests.exceptions.RequestException:
            return None

    def _parse_annual_reports(self, html_content: str) -> List[Dict]:
        """Parse HTML to extract annual report links"""
        if not html_content:
            return []
        soup = BeautifulSoup(html_content, 'html.parser')
        reports = []
        
        for link in soup.select('.annual-reports ul.list-links li a'):
            text = link.text.strip()
            year_match = re.search(r'Financial Year (\d{4})', text)
            if year_match:
                reports.append({
                    'year': int(year_match.group(1)),
                    'type': 'Annual_Report',
                    'url': link['href'],
                    'label': f"FY {year_match.group(1)} Annual Report"
                })
        
        return sorted(reports, key=lambda x: x['year'], reverse=True)

    def get_available_documents(self, stock_symbol: str) -> Dict:
        """Get all available documents for a stock from screener.in"""
        html_content = self._get_webpage_content(stock_symbol)
        if not html_content:
            return {'error': f'Could not fetch data for {stock_symbol}', 'documents': []}
        
        reports = self._parse_annual_reports(html_content)
        return {
            'symbol': stock_symbol,
            'documents': reports,
            'total': len(reports)
        }

    def _download_document(self, url: str) -> Optional[bytes]:
        """Download a document from URL"""
        try:
            headers = {"User-Agent": random.choice(USER_AGENTS)}
            response = requests.get(url, headers=headers, stream=True, 
                                   timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
            response.raise_for_status()
            
            content = response.content
            
            # Validate content
            if content.strip().startswith(b'<!DOCTYPE html') or len(content) < MIN_FILE_SIZE:
                return None
            
            return content
        except requests.exceptions.RequestException:
            return None

    def _extract_text_from_pdf(self, pdf_content: bytes) -> str:
        """Extract text from PDF content"""
        try:
            import pdfplumber
            with pdfplumber.open(BytesIO(pdf_content)) as pdf:
                text_parts = []
                for page in pdf.pages[:50]:  # Limit to first 50 pages
                    text = page.extract_text() or ""
                    text_parts.append(text)
                return "\n\n".join(text_parts)
        except Exception as e:
            return f"Error extracting PDF text: {e}"

    # --- Run Management ---

    def create_run(self, stock_id: int, document_years: List[int], prompt: str) -> int:
        """Create a new research run and start processing in background"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Get stock info
            cursor.execute("""
                SELECT COALESCE(stock_symbol, bse_code) as symbol, stock_name 
                FROM stocks WHERE id = ?
            """, (stock_id,))
            stock = cursor.fetchone()
            
            if not stock:
                raise ValueError("Stock not found")
            
            # Use default prompt if empty
            if not prompt or not prompt.strip():
                prompt = DEFAULT_RESEARCH_PROMPT
            
            # Insert run
            cursor.execute("""
                INSERT INTO document_research_runs 
                (stock_id, stock_symbol, stock_name, document_years, prompt, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, (stock_id, stock['symbol'], stock['stock_name'], 
                  json.dumps(document_years), prompt))
            conn.commit()
            run_id = cursor.lastrowid
            
            # Start background processing
            threading.Thread(
                target=self._process_run,
                args=(run_id,),
                daemon=True
            ).start()
            
            return run_id
        finally:
            conn.close()

    def _process_run(self, run_id: int):
        """Process a research run - download docs, extract text, call LLM"""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        def update_status(status: str, error: str = None):
            cursor.execute("""
                UPDATE document_research_runs
                SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, error, run_id))
            conn.commit()

        try:
            update_status("in_progress")
            
            # Get run details
            cursor.execute("SELECT * FROM document_research_runs WHERE id = ?", (run_id,))
            run = cursor.fetchone()
            if not run:
                return
            
            run = dict(run)
            document_years = json.loads(run['document_years'])
            stock_symbol = run['stock_symbol']
            prompt = run['prompt']
            
            # Fetch available documents
            doc_info = self.get_available_documents(stock_symbol)
            if 'error' in doc_info and not doc_info.get('documents'):
                update_status("error", doc_info['error'])
                return
            
            # Filter to requested years
            docs_to_fetch = [d for d in doc_info['documents'] if d['year'] in document_years]
            
            if not docs_to_fetch:
                update_status("error", f"No annual reports found for years: {document_years}")
                return
            
            # Download and extract text from each document
            doc_texts = []
            for doc in docs_to_fetch:
                content = self._download_document(doc['url'])
                if not content:
                    doc_texts.append(f"### FY {doc['year']} Annual Report\n\n[Failed to download]")
                    continue
                
                text = self._extract_text_from_pdf(content)
                if text.startswith("Error"):
                    doc_texts.append(f"### FY {doc['year']} Annual Report\n\n[{text}]")
                else:
                    # Truncate to keep context manageable
                    truncated = text[:50000]
                    doc_texts.append(f"### FY {doc['year']} Annual Report\n\n{truncated}")
            
            if not any("Failed" not in t and "Error" not in t for t in doc_texts):
                update_status("error", "Could not extract text from any documents")
                return
            
            combined_context = "\n\n---\n\n".join(doc_texts)
            
            user_prompt = f"""Analyzing annual reports for {stock_symbol} ({run['stock_name']}).
Years included: {', '.join(str(y) for y in sorted(document_years, reverse=True))}

{combined_context}"""

            # Call LLM
            try:
                llm_response = self.llm_service.generate(
                    prompt=user_prompt,
                    system_prompt=prompt,
                    thinking_mode=True,
                    max_tokens=12000
                )
                llm_output = llm_response.content
                provider_name = llm_response.provider_name
                model_id = getattr(llm_response, "model_id", None)
            except Exception as e:
                update_status("error", f"LLM generation failed: {e}")
                return
            
            # Save results
            cursor.execute("""
                UPDATE document_research_runs
                SET status = 'done',
                    llm_output = ?,
                    model_provider = ?,
                    model_id = ?,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (llm_output, provider_name, model_id, run_id))
            conn.commit()
            
        except Exception as e:
            update_status("error", f"Unexpected error: {e}")
        finally:
            conn.close()

    def list_runs(self) -> List[Dict]:
        """List all research runs"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT id, stock_id, stock_symbol, stock_name, document_years, 
                       status, model_provider, model_id, error_message,
                       created_at, updated_at
                FROM document_research_runs
                ORDER BY created_at DESC
            """)
            runs = []
            for row in cursor.fetchall():
                run = dict(row)
                run['document_years'] = json.loads(run['document_years'])
                runs.append(run)
            return runs
        finally:
            conn.close()

    def get_run(self, run_id: int) -> Optional[Dict]:
        """Get run details including output"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT * FROM document_research_runs WHERE id = ?
            """, (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            
            run = dict(row)
            run['document_years'] = json.loads(run['document_years'])
            
            # Render HTML for display
            if run.get('llm_output'):
                run['rendered_html'] = self._render_html(run)
            
            return run
        finally:
            conn.close()

    def _render_html(self, run: Dict) -> str:
        """Render LLM output as HTML"""
        content = run.get('llm_output', '')
        
        # Convert markdown to HTML
        try:
            html_content = markdown.markdown(
                content, 
                extensions=['extra', 'tables', 'sane_lists', 'nl2br']
            )
        except Exception:
            html_content = f"<pre>{html.escape(content)}</pre>"
        
        years_str = ", ".join(str(y) for y in sorted(run.get('document_years', []), reverse=True))
        
        return f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6;">
            <div style="margin-bottom: 1rem; padding-bottom: 1rem; border-bottom: 1px solid #333;">
                <h2 style="margin: 0;">{html.escape(run.get('stock_symbol', ''))} - {html.escape(run.get('stock_name', ''))}</h2>
                <p style="color: #888; margin: 0.5rem 0 0 0;">Annual Reports: {html.escape(years_str)}</p>
                <p style="color: #666; font-size: 0.875rem; margin: 0.25rem 0 0 0;">
                    Generated: {run.get('updated_at', '')} | Model: {run.get('model_provider', '')} / {run.get('model_id', '')}
                </p>
            </div>
            <div class="content">
                {html_content}
            </div>
        </div>
        """

    def generate_pdf(self, run_id: int) -> Optional[bytes]:
        """Generate PDF report for a run"""
        run = self.get_run(run_id)
        if not run or run.get('status') != 'done':
            return None
        
        if not HAS_PDF:
            return None
        
        years_str = ", ".join(str(y) for y in sorted(run.get('document_years', []), reverse=True))
        
        # Convert markdown to HTML
        content = run.get('llm_output', '')
        try:
            html_content = markdown.markdown(
                content,
                extensions=['extra', 'tables', 'sane_lists']
            )
        except Exception:
            html_content = f"<pre>{html.escape(content)}</pre>"
        
        html_doc = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{
                    font-family: 'Helvetica', 'Arial', sans-serif;
                    font-size: 11pt;
                    line-height: 1.5;
                    color: #222;
                    margin: 40px;
                }}
                h1 {{ font-size: 18pt; color: #111; margin-bottom: 5px; }}
                h2 {{ font-size: 14pt; color: #333; margin-top: 20px; }}
                h3 {{ font-size: 12pt; color: #444; }}
                .meta {{ color: #666; font-size: 10pt; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid #ddd; }}
                table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 10pt; }}
                th {{ background-color: #f5f5f5; }}
                pre {{ background-color: #f8f8f8; padding: 10px; overflow-x: auto; font-size: 9pt; }}
                code {{ background-color: #f0f0f0; padding: 2px 4px; font-size: 9pt; }}
            </style>
        </head>
        <body>
            <h1>{html.escape(run.get('stock_symbol', ''))} - Document Research</h1>
            <div class="meta">
                <strong>Company:</strong> {html.escape(run.get('stock_name', ''))}<br>
                <strong>Annual Reports:</strong> {html.escape(years_str)}<br>
                <strong>Generated:</strong> {run.get('updated_at', '')}<br>
                <strong>Model:</strong> {run.get('model_provider', '')} / {run.get('model_id', '')}
            </div>
            {html_content}
        </body>
        </html>
        """
        
        result = BytesIO()
        pisa_status = pisa.CreatePDF(html_doc, dest=result)
        
        if pisa_status.err:
            return None
        
        return result.getvalue()
