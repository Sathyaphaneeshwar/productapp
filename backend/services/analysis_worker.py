import threading
import time
import sqlite3
import os
import sys
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.config import DATABASE_PATH
from services.prompt_service import PromptService
from services.transcript_service import TranscriptService
from services.llm.llm_service import LLMService
from services.email_service import EmailService

class AnalysisWorker:
    def __init__(self):
        self.prompt_service = PromptService()
        self.transcript_service = TranscriptService()
        self.llm_service = LLMService()
        self.email_service = EmailService()
        self.db_path = str(DATABASE_PATH)

    def get_db_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def start_analysis_job(self, stock_id: int) -> str:
        """
        Starts the analysis job in a background thread.
        Returns a Job ID (for now, we'll just return a timestamp-based ID).
        """
        job_id = f"job_{stock_id}_{int(time.time())}"
        
        # Start background thread
        thread = threading.Thread(
            target=self._process_analysis_job,
            args=(stock_id, job_id)
        )
        thread.daemon = True # Daemon thread so it doesn't block app exit
        thread.start()
        
        return job_id

    def _process_analysis_job(self, stock_id: int, job_id: str):
        """
        Internal method running in background thread.
        """
        print(f"[{job_id}] Starting analysis for stock {stock_id}")
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # 1. Get Stock Symbol
            cursor.execute("SELECT stock_symbol FROM stocks WHERE id = ?", (stock_id,))
            stock = cursor.fetchone()
            if not stock:
                print(f"[{job_id}] Stock not found!")
                return
            
            symbol = stock['stock_symbol']
            print(f"[{job_id}] Processing symbol: {symbol}")

            # 2. Fetch Available Transcripts
            print(f"[{job_id}] Fetching transcripts for {symbol}...")
            transcripts = self.transcript_service.fetch_available_transcripts(symbol)
            
            if not transcripts:
                print(f"[{job_id}] No transcripts found for {symbol}")
                return

            # For now, we just take the first one (latest)
            latest_transcript = transcripts[0]
            print(f"[{job_id}] Found transcript: {latest_transcript.title}")

            # Check if we already have a transcript for this quarter/year combination
            # OR the exact same source_url (to handle URL changes for same quarter)
            cursor.execute("""
                SELECT id, source_url FROM transcripts 
                WHERE stock_id = ? AND quarter = ? AND year = ?
            """, (stock_id, latest_transcript.quarter, latest_transcript.year))
            
            transcript_row = cursor.fetchone()
            
            transcript_text = ""
            
            if not transcript_row:
                print(f"[{job_id}] Downloading and extracting text...")
                transcript_text = self.transcript_service.download_and_extract(latest_transcript.source_url)
                
                # Save to DB
                cursor.execute("""
                    INSERT INTO transcripts (stock_id, quarter, year, source_url, content_path)
                    VALUES (?, ?, ?, ?, ?)
                """, (stock_id, latest_transcript.quarter, latest_transcript.year, latest_transcript.source_url, "placeholder_path"))
                conn.commit()
                transcript_id = cursor.lastrowid
            else:
                print(f"[{job_id}] Using existing transcript record.")
                transcript_id = transcript_row['id']
                
                # Update source_url if it changed (API might return new URL for same quarter)
                if transcript_row['source_url'] != latest_transcript.source_url:
                    print(f"[{job_id}] Updating transcript URL (changed from API)...")
                    cursor.execute("""
                        UPDATE transcripts SET source_url = ? WHERE id = ?
                    """, (latest_transcript.source_url, transcript_id))
                    conn.commit()
                
                # Re-download text for analysis
                print(f"[{job_id}] Downloading text for analysis...")
                transcript_text = self.transcript_service.download_and_extract(latest_transcript.source_url)

            # 3. Resolve Prompt
            print(f"[{job_id}] Resolving prompt...")
            system_prompt = self.prompt_service.resolve_prompt(stock_id)
            print(f"[{job_id}] Prompt resolved: {system_prompt[:50]}...")

            # 4. Call LLM
            print(f"[{job_id}] Calling LLM...")
            
            try:
                llm_response = self.llm_service.generate(
                    prompt=f"Here is the transcript text:\n\n{transcript_text}",
                    system_prompt=system_prompt,
                    thinking_mode=True,  # Default to thinking mode for analysis
                    max_tokens=12000     # Request longer analyses by default
                )
                llm_output = llm_response.content
                provider_name = llm_response.provider_name
                
            except Exception as e:
                print(f"[{job_id}] LLM generation failed: {e}")
                raise e

            # 5. Save Results
            print(f"[{job_id}] Saving results...")
            cursor.execute("""
                INSERT INTO transcript_analyses (transcript_id, prompt_snapshot, llm_output, model_provider)
                VALUES (?, ?, ?, ?)
            """, (transcript_id, system_prompt, llm_output, provider_name))
            conn.commit()
            
            # 6. Send Email
            print(f"[{job_id}] Sending emails...")
            email_list = self.email_service.get_active_email_list()
            if email_list:
                # Get stock name
                cursor.execute("SELECT stock_name FROM stocks WHERE id = ?", (stock_id,))
                stock_name = cursor.fetchone()['stock_name']
                
                # Get model name from LLM response
                model_name = llm_response.model_id if hasattr(llm_response, 'model_id') else provider_name
                
                for email in email_list:
                    try:
                        self.email_service.send_analysis_email(
                            to_email=email,
                            stock_symbol=symbol,
                            stock_name=stock_name,
                            quarter=latest_transcript.quarter,
                            year=latest_transcript.year,
                            analysis_content=llm_output,
                            model_provider=provider_name,
                            model_name=model_name,
                            transcript_url=latest_transcript.source_url
                        )
                        print(f"[{job_id}] Email sent to {email}")
                    except Exception as e:
                        print(f"[{job_id}] Failed to send email to {email}: {e}")
            else:
                print(f"[{job_id}] No active email recipients found.")
            conn.commit()
            
            print(f"[{job_id}] Job complete.")

        except Exception as e:
            print(f"[{job_id}] Job failed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            conn.close()
