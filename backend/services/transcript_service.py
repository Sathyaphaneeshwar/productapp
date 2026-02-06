import logging
import requests
import os
import sys
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit, quote
import sqlite3

logger = logging.getLogger(__name__)

# Add parent directory to path
from config import DATABASE_PATH
from db import get_db_connection
from services.key_service import KeyService

@dataclass
class TranscriptMetadata:
    stock_symbol: str
    quarter: str
    year: int
    source_url: str
    title: str
    isin: str
    event_date: str = None  # For upcoming calls

class TranscriptService:
    def __init__(self):
        self.key_service = KeyService()
        self.base_url = "https://www.tijoristack.ai/api/v1"

    def get_db_connection(self):
        return get_db_connection(DATABASE_PATH)

    def _get_headers(self) -> dict:
        api_key = self.key_service.get_api_key('tijori')
        if not api_key:
            raise ValueError("Tijori API key not found or inactive.")
        return {
            'Authorization': f'Bearer {api_key}',
            'accept': 'application/json'
        }

    def _get_isin_from_symbol(self, stock_symbol: str) -> Optional[str]:
        """Resolve ISIN using either NSE symbol or BSE code."""
        if not stock_symbol:
            return None
        stock_symbol = stock_symbol.strip()
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT isin_number FROM stocks 
                WHERE UPPER(stock_symbol) = UPPER(?) OR bse_code = ?
                LIMIT 1
            """, (stock_symbol, stock_symbol))
            result = cursor.fetchone()
            return result['isin_number'] if result else None
        finally:
            conn.close()

    def _parse_event_time(self, event_time_str: str) -> Optional[datetime]:
        if not event_time_str:
            return None
        try:
            return datetime.fromisoformat(str(event_time_str).replace('Z', '+00:00'))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(str(event_time_str), fmt)
                except ValueError:
                    continue
        return None

    def _sanitize_url(self, url: str) -> str:
        """Encode unsafe URL characters (notably spaces) while preserving structure."""
        if not url:
            return url
        raw = str(url).strip()
        if not raw:
            return raw
        try:
            parts = urlsplit(raw)
            if not parts.scheme or not parts.netloc:
                return raw.replace(' ', '%20')
            path = quote(parts.path, safe='/%')
            query = quote(parts.query, safe='=&%')
            fragment = quote(parts.fragment, safe='%')
            return urlunsplit((parts.scheme, parts.netloc, path, query, fragment))
        except Exception:
            return raw.replace(' ', '%20')

    def _calculate_fy_quarter(self, event_time_str: str) -> tuple[str, int]:
        """
        Calculates Indian Financial Year and Quarter for the EARNINGS PERIOD
        that a concall covers (not the date the concall happens).
        
        Concalls are held AFTER the quarter ends to discuss that quarter's results:
        - Concall in Apr-Jun → discusses Q4 results (Jan-Mar)
        - Concall in Jul-Sep → discusses Q1 results (Apr-Jun)
        - Concall in Oct-Dec → discusses Q2 results (Jul-Sep)
        - Concall in Jan-Mar → discusses Q3 results (Oct-Dec)
        
        Example: Concall on Nov 21, 2025 → Q2 FY26 earnings (Jul-Sep 2025)
        """
        dt = event_time_str if isinstance(event_time_str, datetime) else self._parse_event_time(event_time_str)
        if not dt:
            raise ValueError(f"Invalid concall_event_time: {event_time_str}")
        month = dt.month
        year = dt.year
        
        # Determine which quarter's earnings this concall is discussing
        # (the PREVIOUS quarter from the concall date)
        if 4 <= month <= 6:
            # Concall in Q1 (Apr-Jun) → discussing Q4 results (Jan-Mar)
            quarter = "Q4"
            fy = year  # Q4 of current calendar year's FY
        elif 7 <= month <= 9:
            # Concall in Q2 (Jul-Sep) → discussing Q1 results (Apr-Jun)
            quarter = "Q1"
            fy = year + 1  # Q1 of next FY (e.g., Apr 2025 = Q1 FY26)
        elif 10 <= month <= 12:
            # Concall in Q3 (Oct-Dec) → discussing Q2 results (Jul-Sep)
            quarter = "Q2"
            fy = year + 1  # Q2 of next FY (e.g., Jul 2025 = Q2 FY26)
        else:  # 1 <= month <= 3
            # Concall in Q4 (Jan-Mar) → discussing Q3 results (Oct-Dec)
            quarter = "Q3"
            fy = year  # Q3 of current FY (e.g., Oct 2025 = Q3 FY26)
            
        return quarter, fy

    def fetch_available_transcripts(self, stock_symbol: str) -> List[TranscriptMetadata]:
        """
        Fetches the latest transcript for a given stock using Tijori API.
        """
        isin = self._get_isin_from_symbol(stock_symbol)
        if not isin:
            logger.warning("ISIN not found for %s", stock_symbol)
            return []

        url = f"{self.base_url}/concalls/list"
        params = {
            'page': 1,
            'isin': isin,
            'mcap': 'all',
            'upcoming': 'false',
            'page_size': 5  # Fetch last 5 quarters of transcripts
        }

        try:
            response = requests.get(url, headers=self._get_headers(), params=params)
            response.raise_for_status()
            data = response.json()
            
            results = []
            for item in data.get('data', []):
                transcript_url = item.get('transcript')
                if not transcript_url:
                    continue

                event_time = item.get('concall_event_time') or item.get('event_time') or item.get('event_date')
                parsed_time = self._parse_event_time(event_time)
                if not parsed_time:
                    logger.warning("Missing/invalid concall_event_time for %s: %s", stock_symbol, event_time)
                    continue

                try:
                    quarter, fy = self._calculate_fy_quarter(parsed_time)
                except Exception as e:
                    logger.warning("Failed to calculate quarter for %s: %s (%s)", stock_symbol, event_time, e)
                    continue

                results.append((
                    parsed_time,
                    TranscriptMetadata(
                        stock_symbol=stock_symbol,
                        quarter=quarter,
                        year=fy,
                        source_url=transcript_url,
                        title=f"{quarter} FY{fy} Earnings Call",
                        isin=isin
                    )
                ))

            # Ensure newest transcripts come first
            results.sort(key=lambda item: item[0], reverse=True)
            return [item[1] for item in results]

        except Exception as e:
            logger.warning("Error fetching transcripts from Tijori: %s", e)
            # Fallback to empty list or re-raise depending on requirement
            return []

    def download_and_extract(self, url: str) -> str:
        """
        Downloads the PDF from the URL and extracts text.
        """
        import tempfile
        from pypdf import PdfReader
        
        try:
            safe_url = self._sanitize_url(url)
            if safe_url != url:
                logger.debug("Sanitized URL for download: %s", safe_url)
            logger.info("Downloading PDF from %s", safe_url)
            # Some providers block default Python user agents; use a browsery UA
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            }
            session = requests.Session()
            session.headers.update(headers)
            response = session.get(safe_url, timeout=30)
            if response.status_code == 403:
                # Retry once with referrer to appease some CDNs
                session.headers.update({"Referer": safe_url.rsplit('/', 1)[0]})
                response = session.get(safe_url, timeout=30)
            response.raise_for_status()
            
            # Save to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                tmp_file.write(response.content)
                tmp_path = tmp_file.name
            
            logger.info("Extracting text from PDF")
            reader = PdfReader(tmp_path)
            
            # Extract text from all pages
            text_content = []
            for page_num, page in enumerate(reader.pages):
                text = page.extract_text()
                if text:
                    text_content.append(text)
            
            # Clean up temp file
            import os
            os.unlink(tmp_path)
            
            full_text = "\n\n".join(text_content)
            logger.info("Extracted %d characters from %d pages", len(full_text), len(reader.pages))
            
            return full_text
            
        except Exception as e:
            logger.warning("Error downloading/extracting PDF: %s", e)
            return f"Error extracting text: {str(e)}"

    def validate_api_key(self) -> bool:
        """
        Validates the stored Tijori API key by making a lightweight request.
        """
        try:
            # We'll just try to fetch 1 item. If it succeeds (200 OK), the key is valid.
            # If it fails (401/403), it's invalid.
            # We use a dummy ISIN or just list call if possible, but list requires ISIN usually.
            # Let's try to fetch upcoming calls which doesn't strictly require ISIN in the example provided earlier?
            # Actually the user provided: https://www.tijoristack.ai/api/v1/concalls/list?page=1&mcap=all&upcoming=true&page_size=20
            # This doesn't require ISIN. Perfect for validation.
            
            url = f"{self.base_url}/concalls/list"
            params = {
                'page': 1,
                'mcap': 'all',
                'upcoming': 'true',
                'page_size': 1
            }
            response = requests.get(url, headers=self._get_headers(), params=params)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.warning("API validation failed: %s", e)
            return False

    def get_upcoming_calls(self, stock_symbol: str = None) -> List[TranscriptMetadata]:
        """
        Fetches upcoming conference calls from Tijori API.
        If stock_symbol is provided, filters by that stock's ISIN.
        Otherwise returns all upcoming calls.
        """
        url = f"{self.base_url}/concalls/list"
        params = {
            'page': 1,
            'mcap': 'all',
            'upcoming': 'true',
            'page_size': 100  # Fetch more to get all upcoming
        }
        
        if stock_symbol:
            isin = self._get_isin_from_symbol(stock_symbol)
            if not isin:
                return []
            params['isin'] = isin
            params['page_size'] = 20

        try:
            response = requests.get(url, headers=self._get_headers(), params=params)
            response.raise_for_status()
            data = response.json()
            
            results = []
            for item in data.get('data', []):
                status = (item.get('status') or '').strip().lower()
                if status and status != 'upcoming':
                    continue

                # For upcoming, we might not have quarter/year yet
                # We'll calculate from event_date
                event_time = item.get('concall_event_time') or item.get('event_time') or item.get('event_date')
                if event_time:
                    quarter, fy = self._calculate_fy_quarter(event_time)

                    results.append(TranscriptMetadata(
                        stock_symbol=item['company_info']['name'],
                        quarter=quarter,
                        year=fy,
                        source_url=None,  # Not available yet
                        title=f"{quarter} FY{fy} Earnings Call (Upcoming)",
                        isin=item['company_info']['isin'],
                        event_date=event_time  # Include event date
                    ))
            return results

        except Exception as e:
            logger.warning("Error fetching upcoming calls from Tijori: %s", e)
            return []
