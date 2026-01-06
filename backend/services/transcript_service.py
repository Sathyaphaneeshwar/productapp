import requests
import os
import sys
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime
import sqlite3

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.config import DATABASE_PATH
from backend.services.key_service import KeyService

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
        conn = sqlite3.connect(str(DATABASE_PATH))
        conn.row_factory = sqlite3.Row
        return conn

    def _get_headers(self) -> dict:
        api_key = self.key_service.get_api_key('tijori')
        if not api_key:
            raise ValueError("Tijori API key not found or inactive.")
        return {
            'Authorization': f'Bearer {api_key}',
            'accept': 'application/json'
        }

    def _get_isin_from_symbol(self, stock_symbol: str) -> Optional[str]:
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT isin_number FROM stocks WHERE stock_symbol = ?", (stock_symbol,))
            result = cursor.fetchone()
            return result['isin_number'] if result else None
        finally:
            conn.close()

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
        dt = datetime.fromisoformat(event_time_str.replace('Z', '+00:00'))
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
            print(f"ISIN not found for {stock_symbol}")
            return []

        url = f"{self.base_url}/concalls/list"
        params = {
            'page': 1,
            'isin': isin,
            'mcap': 'all',
            'upcoming': 'false',
            'page_size': 1 # We only want the latest one for now
        }

        try:
            response = requests.get(url, headers=self._get_headers(), params=params)
            response.raise_for_status()
            data = response.json()
            
            results = []
            for item in data.get('data', []):
                if item.get('status') == 'recorded' and item.get('transcript'):
                    quarter, fy = self._calculate_fy_quarter(item['concall_event_time'])
                    
                    results.append(TranscriptMetadata(
                        stock_symbol=stock_symbol,
                        quarter=quarter,
                        year=fy,
                        source_url=item['transcript'],
                        title=f"{quarter} FY{fy} Earnings Call",
                        isin=isin
                    ))
            return results

        except Exception as e:
            print(f"Error fetching transcripts from Tijori: {e}")
            # Fallback to empty list or re-raise depending on requirement
            return []

    def download_and_extract(self, url: str) -> str:
        """
        Downloads the PDF from the URL and extracts text.
        """
        import tempfile
        from pypdf import PdfReader
        
        try:
            print(f"Downloading PDF from {url}...")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            # Save to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                tmp_file.write(response.content)
                tmp_path = tmp_file.name
            
            print(f"Extracting text from PDF...")
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
            print(f"Extracted {len(full_text)} characters from {len(reader.pages)} pages")
            
            return full_text
            
        except Exception as e:
            print(f"Error downloading/extracting PDF: {e}")
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
            print(f"API Validation failed: {e}")
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
                if item.get('status') == 'Upcoming':
                    # For upcoming, we might not have quarter/year yet
                    # We'll calculate from event_date
                    event_time = item.get('concall_event_time')
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
            print(f"Error fetching upcoming calls from Tijori: {e}")
            return []

