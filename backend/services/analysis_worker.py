import threading
import time
import sqlite3
import os
import sys
from datetime import datetime
from typing import Optional

# Add parent directory to path
from config import DATABASE_PATH
from db import get_db_connection
from services.prompt_service import PromptService
from services.transcript_service import TranscriptService
from services.llm.llm_service import LLMService
from services.email_service import EmailService

import logging

logger = logging.getLogger(__name__)

class AnalysisWorker:
    def __init__(self):
        self.prompt_service = PromptService()
        self.transcript_service = TranscriptService()
        self.llm_service = LLMService()
        self.email_service = EmailService()
        self.db_path = str(DATABASE_PATH)

    def get_db_connection(self):
        return get_db_connection(self.db_path)

    def _analysis_exists_for_quarter(self, cursor, stock_id: int, quarter: str, year: int) -> bool:
        cursor.execute("""
            SELECT 1
            FROM transcript_analyses ta
            JOIN transcripts t ON t.id = ta.transcript_id
            WHERE t.stock_id = ? AND t.quarter = ? AND t.year = ?
            LIMIT 1
        """, (stock_id, quarter, year))
        return cursor.fetchone() is not None

    def _is_in_active_group(self, cursor, stock_id: int) -> bool:
        cursor.execute("""
            SELECT 1
            FROM group_stocks gs
            JOIN groups g ON g.id = gs.group_id
            WHERE gs.stock_id = ? AND g.is_active = 1
            LIMIT 1
        """, (stock_id,))
        return cursor.fetchone() is not None

    def _set_analysis_status(self, cursor, transcript_id: int, status: str, error: Optional[str] = None):
        cursor.execute("""
            UPDATE transcripts
            SET analysis_status = ?, analysis_error = ?
            WHERE id = ?
        """, (status, error, transcript_id))

    def start_analysis_job(self, stock_id: int, quarter: Optional[str] = None, year: Optional[int] = None, force: bool = False) -> str:
        """
        Starts the analysis job in a background thread.
        Returns a Job ID (for now, we'll just return a timestamp-based ID).
        """
        job_id = f"job_{stock_id}_{int(time.time())}"
        
        # Start background thread
        thread = threading.Thread(
            target=self._process_analysis_job,
            args=(stock_id, job_id, quarter, year, force)
        )
        thread.daemon = True # Daemon thread so it doesn't block app exit
        thread.start()
        
        return job_id

    def _process_analysis_job(self, stock_id: int, job_id: str, quarter: Optional[str] = None, year: Optional[int] = None, force: bool = False):
        """
        Internal method running in background thread.
        """
        logger.info("[%s] Starting analysis for stock %s", job_id, stock_id)
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # 1. Get Stock Symbol
            cursor.execute("SELECT stock_symbol, bse_code FROM stocks WHERE id = ?", (stock_id,))
            stock = cursor.fetchone()
            if not stock:
                logger.warning("[%s] Stock not found", job_id)
                return

            # Only analyze stocks that are currently in the watchlist
            cursor.execute("SELECT 1 FROM watchlist_items WHERE stock_id = ? LIMIT 1", (stock_id,))
            if cursor.fetchone() is None:
                logger.info("[%s] Stock %s not in watchlist; skipping analysis job", job_id, stock_id)
                return

            if self._is_in_active_group(cursor, stock_id):
                logger.info("[%s] Stock %s is in an active group; skipping analysis job", job_id, stock_id)
                return
            
            symbol = stock['stock_symbol'] or stock['bse_code']
            if not symbol:
                logger.warning("[%s] No symbol/bse_code found for stock %s", job_id, stock_id)
                return
            logger.info("[%s] Processing symbol: %s", job_id, symbol)

            # 2. Resolve which transcript to analyze
            transcript_id = None
            transcript_text = ""
            target_quarter = quarter
            target_year = year
            transcript_source_url = None
            analysis_started = False
            analysis_completed = False

            def mark_analysis_in_progress():
                nonlocal analysis_started
                if transcript_id and not analysis_started:
                    self._set_analysis_status(cursor, transcript_id, 'in_progress', None)
                    conn.commit()
                    analysis_started = True

            if quarter and year:
                logger.info("[%s] Using requested quarter/year: %s %s", job_id, quarter, year)
                cursor.execute("""
                    SELECT id, quarter, year, source_url, status 
                    FROM transcripts 
                    WHERE stock_id = ? AND quarter = ? AND year = ?
                    LIMIT 1
                """, (stock_id, quarter, year))
                transcript_row = cursor.fetchone()

                if not transcript_row:
                    logger.warning("[%s] Transcript not found for %s %s %s", job_id, symbol, quarter, year)
                    return

                if transcript_row['status'] != 'available':
                    logger.warning("[%s] Transcript not available (status=%s) for %s %s %s", job_id, transcript_row['status'], symbol, quarter, year)
                    return

                if not transcript_row['source_url']:
                    logger.warning("[%s] Transcript has no source_url for %s %s %s", job_id, symbol, quarter, year)
                    return

                if not force and self._analysis_exists_for_quarter(cursor, stock_id, transcript_row['quarter'], transcript_row['year']):
                    logger.debug("[%s] Analysis already exists for %s %s %s; skipping", job_id, symbol, quarter, year)
                    return

                transcript_id = transcript_row['id']
                target_quarter = transcript_row['quarter']
                target_year = transcript_row['year']
                transcript_source_url = transcript_row['source_url']
                
                # Check if analysis already exists for this transcript (prevents duplicate emails)
                cursor.execute("""
                    SELECT id FROM transcript_analyses WHERE transcript_id = ?
                """, (transcript_id,))
                if cursor.fetchone() and not force:
                    logger.debug("[%s] Analysis already exists for %s %s %s; skipping", job_id, symbol, quarter, year)
                    return

                mark_analysis_in_progress()
                logger.info("[%s] Downloading and extracting text...", job_id)
                transcript_text = self.transcript_service.download_and_extract(transcript_row['source_url'])

            else:
                # Fallback to latest transcript from provider
                logger.info("[%s] Fetching transcripts for %s...", job_id, symbol)
                transcripts = self.transcript_service.fetch_available_transcripts(symbol)
                
                if not transcripts:
                    logger.warning("[%s] No transcripts found for %s", job_id, symbol)
                    return

                latest_transcript = transcripts[0]
                target_quarter = latest_transcript.quarter
                target_year = latest_transcript.year
                transcript_source_url = latest_transcript.source_url
                logger.info("[%s] Found transcript: %s", job_id, latest_transcript.title)

                if not force and self._analysis_exists_for_quarter(cursor, stock_id, target_quarter, target_year):
                    logger.debug("[%s] Analysis already exists for %s %s %s; skipping", job_id, symbol, target_quarter, target_year)
                    return

                # Check if we already have a transcript for this quarter/year combination
                # OR the exact same source_url (to handle URL changes for same quarter)
                cursor.execute("""
                    SELECT id, source_url FROM transcripts 
                    WHERE stock_id = ? AND quarter = ? AND year = ?
                """, (stock_id, latest_transcript.quarter, latest_transcript.year))
                
                transcript_row = cursor.fetchone()
                
                if not transcript_row:
                    logger.info("[%s] Downloading and extracting text...", job_id)
                    transcript_text = self.transcript_service.download_and_extract(latest_transcript.source_url)

                    # Save to DB - set status to 'available' since we have a valid source_url
                    cursor.execute("""
                        INSERT INTO transcripts (stock_id, quarter, year, source_url, status, content_path)
                        VALUES (?, ?, ?, ?, 'available', ?)
                    """, (stock_id, latest_transcript.quarter, latest_transcript.year, latest_transcript.source_url, "placeholder_path"))
                    conn.commit()
                    transcript_id = cursor.lastrowid
                    mark_analysis_in_progress()
                else:
                    logger.info("[%s] Using existing transcript record", job_id)
                    transcript_id = transcript_row['id']
                    
                    # Check if analysis already exists for this transcript (prevents duplicate emails)
                    cursor.execute("""
                        SELECT id FROM transcript_analyses WHERE transcript_id = ?
                    """, (transcript_id,))
                    if cursor.fetchone() and not force:
                        logger.debug("[%s] Analysis already exists for %s %s %s; skipping", job_id, symbol, latest_transcript.quarter, latest_transcript.year)
                        return
                    
                    mark_analysis_in_progress()
                    # Update source_url if it changed (API might return new URL for same quarter)
                    # Also ensure status is 'available' since we have a valid transcript URL
                    if transcript_row['source_url'] != latest_transcript.source_url:
                        logger.info("[%s] Updating transcript URL and status (changed from API)...", job_id)
                        cursor.execute("""
                        UPDATE transcripts SET source_url = ?, status = 'available' WHERE id = ?
                    """, (latest_transcript.source_url, transcript_id))
                    else:
                        # Even if URL didn't change, ensure status is 'available' (fixes edge case where
                        # transcript was marked 'upcoming' but now has a valid source_url)
                        cursor.execute("""
                        UPDATE transcripts SET status = 'available' WHERE id = ? AND status != 'available'
                    """, (transcript_id,))
                    conn.commit()
                    transcript_source_url = latest_transcript.source_url
                    
                    # Re-download text for analysis
                    logger.info("[%s] Downloading text for analysis...", job_id)
                    transcript_text = self.transcript_service.download_and_extract(latest_transcript.source_url)

            # 3. Resolve Prompt
            logger.info("[%s] Resolving prompt...", job_id)
            system_prompt = self.prompt_service.resolve_prompt(stock_id)
            logger.debug("[%s] Prompt resolved (length=%d)", job_id, len(system_prompt))

            # 4. Call LLM
            logger.info("[%s] Calling LLM...", job_id)
            
            try:
                llm_response = self.llm_service.generate(
                    prompt=f"Here is the transcript text:\n\n{transcript_text}",
                    system_prompt=system_prompt,
                    thinking_mode=True,  # Default to thinking mode for analysis
                    max_tokens=12000,    # Request longer analyses by default
                    task_type='watchlist',
                )
                llm_output = llm_response.content
                provider_name = llm_response.provider_name
                
            except Exception as e:
                logger.exception("[%s] LLM generation failed", job_id)
                raise e

            # 5. Save Results
            logger.info("[%s] Saving results...", job_id)
            cursor.execute("""
                INSERT INTO transcript_analyses (transcript_id, prompt_snapshot, llm_output, model_provider)
                VALUES (?, ?, ?, ?)
            """, (transcript_id, system_prompt, llm_output, provider_name))
            conn.commit()
            new_analysis_id = cursor.lastrowid
            if transcript_id:
                self._set_analysis_status(cursor, transcript_id, 'done', None)
                conn.commit()
                analysis_completed = True

            if force:
                cursor.execute("""
                    DELETE FROM transcript_analyses
                    WHERE transcript_id = ? AND id != ?
                """, (transcript_id, new_analysis_id))
                conn.commit()
            
            # 6. Send Email
            logger.info("[%s] Sending emails...", job_id)
            email_list = self.email_service.get_active_email_list()
            if email_list:
                cursor.execute("SELECT 1 FROM watchlist_items WHERE stock_id = ? LIMIT 1", (stock_id,))
                if cursor.fetchone() is None:
                    logger.info("[%s] Stock %s not in watchlist; skipping analysis emails", job_id, stock_id)
                else:
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
                                quarter=target_quarter,
                                year=target_year,
                                analysis_content=llm_output,
                                model_provider=provider_name,
                                model_name=model_name,
                                transcript_url=transcript_source_url
                            )
                            logger.info("[%s] Email sent to %s", job_id, email)
                        except Exception as e:
                            logger.warning("[%s] Failed to send email to %s: %s", job_id, email, e)
            else:
                logger.info("[%s] No active email recipients found", job_id)
            conn.commit()
            
            logger.info("[%s] Job complete", job_id)

        except Exception as e:
            logger.exception("[%s] Job failed", job_id)
            if transcript_id and not analysis_completed:
                try:
                    error_message = str(e)
                    if len(error_message) > 500:
                        error_message = error_message[:500]
                    self._set_analysis_status(cursor, transcript_id, 'error', error_message)
                    conn.commit()
                except Exception as status_error:
                    logger.warning("[%s] Failed to record analysis error: %s", job_id, status_error)
        finally:
            conn.close()
