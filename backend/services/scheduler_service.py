import threading
import time
import sqlite3
import os
import sys
from datetime import datetime

from config import DATABASE_PATH
from services.transcript_service import TranscriptService
from services.analysis_worker import AnalysisWorker
from services.group_research_service import GroupResearchService

class SchedulerService:
    def __init__(self, poll_interval_seconds=3600):  # Default: 1 hour
        self.poll_interval = poll_interval_seconds
        self.transcript_service = TranscriptService()
        self.analysis_worker = AnalysisWorker()
        self.group_research_service = GroupResearchService()
        self.running = False
        self.thread = None

    def get_db_connection(self):
        conn = sqlite3.connect(str(DATABASE_PATH))
        conn.row_factory = sqlite3.Row
        return conn

    def _process_stock(self, cursor, conn, stock_row):
        """
        Handles transcript availability/upcoming checks for a single stock.
        Shared by the scheduler loop and one-off triggers.
        """
        stock_id = stock_row['id']
        symbol = stock_row['stock_symbol']

        print(f"[Scheduler] Checking {symbol}...")

        # Fetch available transcripts
        transcripts = self.transcript_service.fetch_available_transcripts(symbol)
        for transcript in transcripts:
            # Check by source_url instead of quarter/year to prevent duplicates
            # Same PDF URL = same transcript, regardless of calculated quarter
            cursor.execute("""
                SELECT id, status, quarter, year FROM transcripts 
                WHERE stock_id = ? AND source_url = ?
            """, (stock_id, transcript.source_url))
            
            existing = cursor.fetchone()
            
            # Fallback: also check for upcoming by quarter/year if not found by URL
            # This handles cases where the transcript URL changed when it became available
            if not existing:
                cursor.execute("""
                    SELECT id, status, quarter, year FROM transcripts 
                    WHERE stock_id = ? AND quarter = ? AND year = ? AND status = 'upcoming'
                """, (stock_id, transcript.quarter, transcript.year))
                existing = cursor.fetchone()
            
            if not existing:
                print(f"[Scheduler] New transcript found for {symbol}: {transcript.title}")
                cursor.execute("""
                    INSERT INTO transcripts (stock_id, quarter, year, source_url, status)
                    VALUES (?, ?, ?, ?, 'available')
                """, (stock_id, transcript.quarter, transcript.year, transcript.source_url))
                conn.commit()
                
                print(f"[Scheduler] Auto-triggering analysis for {symbol}")
                try:
                    job_id = self.analysis_worker.start_analysis_job(stock_id)
                    print(f"[Scheduler] Analysis job started: {job_id}")
                except Exception as e:
                    print(f"[Scheduler] Failed to start analysis: {e}")
            
            elif existing['status'] == 'upcoming':
                print(f"[Scheduler] Upcoming transcript now available for {symbol}: {transcript.title}")
                cursor.execute("""
                    UPDATE transcripts 
                    SET status = 'available', source_url = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (transcript.source_url, existing['id']))
                conn.commit()
                
                print(f"[Scheduler] Auto-triggering analysis for {symbol}")
                try:
                    job_id = self.analysis_worker.start_analysis_job(stock_id)
                    print(f"[Scheduler] Analysis job started: {job_id}")
                except Exception as e:
                    print(f"[Scheduler] Failed to start analysis: {e}")

        # Fetch upcoming calls
        upcoming = self.transcript_service.get_upcoming_calls(symbol)
        for call in upcoming:
            cursor.execute("""
                SELECT id FROM transcripts 
                WHERE stock_id = ? AND quarter = ? AND year = ? AND status = 'upcoming'
            """, (stock_id, call.quarter, call.year))
            
            if not cursor.fetchone():
                print(f"[Scheduler] New upcoming call for {symbol}: {call.title} on {call.event_date}")
                cursor.execute("""
                    INSERT INTO transcripts (stock_id, quarter, year, status, event_date)
                    VALUES (?, ?, ?, 'upcoming', ?)
                """, (stock_id, call.quarter, call.year, call.event_date))
                conn.commit()

    def poll_watchlist(self):
        """
        Polls Tijori API for all stocks in the watchlist.
        Updates transcripts table with new available transcripts or upcoming calls.
        """
        print(f"[Scheduler] Starting watchlist poll at {datetime.now()}")
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Get all stocks in watchlist
            cursor.execute("""
                SELECT s.id, s.stock_symbol, s.isin_number 
                FROM stocks s
                INNER JOIN watchlist_items w ON s.id = w.stock_id
            """)
            
            watchlist_stocks = cursor.fetchall()
            print(f"[Scheduler] Found {len(watchlist_stocks)} stocks in watchlist")
            
            for stock in watchlist_stocks:
                self._process_stock(cursor, conn, stock)
                    
            print(f"[Scheduler] Poll completed at {datetime.now()}")
            
        except Exception as e:
            print(f"[Scheduler] Error during poll: {e}")
        finally:
            conn.close()

    def check_and_schedule_stock(self, stock_id: int):
        """
        Runs the transcript check for a single stock immediately.
        Intended for use right after adding to watchlist/group.
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, stock_symbol FROM stocks WHERE id = ?", (stock_id,))
            stock = cursor.fetchone()
            if not stock:
                print(f"[Scheduler] Stock id {stock_id} not found for immediate check")
                return
            self._process_stock(cursor, conn, stock)
        except Exception as e:
            print(f"[Scheduler] Error checking stock {stock_id}: {e}")
        finally:
            conn.close()

    def trigger_check_for_stock(self, stock_id: int):
        """
        Fire-and-forget background check for a single stock to avoid blocking HTTP responses.
        """
        threading.Thread(target=self.check_and_schedule_stock, args=(stock_id,), daemon=True).start()

    def _run_scheduler(self):
        """Background thread that runs the polling loop."""
        while self.running:
            try:
                self.poll_watchlist()
                # After watchlist polling, check if any group is ready for a deep research run
                self.group_research_service.check_and_trigger_runs()
            except Exception as e:
                print(f"[Scheduler] Unexpected error: {e}")
            
            # Sleep for the configured interval
            time.sleep(self.poll_interval)

    def start(self):
        """Starts the background scheduler."""
        if self.running:
            print("[Scheduler] Already running")
            return
        
        print(f"[Scheduler] Starting with {self.poll_interval}s interval")
        self.running = True
        self.thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.thread.start()

    def stop(self):
        """Stops the background scheduler."""
        print("[Scheduler] Stopping...")
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
