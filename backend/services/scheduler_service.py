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


def _get_latest_quarter():
    """
    Returns (quarter, year) for the latest quarter that should be auto-analyzed.
    This is the 'previous' quarter since earnings are released after the quarter ends.
    For January 2026 (Q4 FY26), this returns Q3 FY26.
    """
    now = datetime.now()
    month = now.month
    year = now.year
    
    # Determine current FY quarter
    if 4 <= month <= 6:
        current_q, current_fy = "Q1", year + 1
    elif 7 <= month <= 9:
        current_q, current_fy = "Q2", year + 1
    elif 10 <= month <= 12:
        current_q, current_fy = "Q3", year + 1
    else:  # 1-3 (Jan, Feb, Mar)
        current_q, current_fy = "Q4", year
    
    # Get previous quarter (the one being released)
    if current_q == "Q1":
        return "Q4", current_fy - 1
    elif current_q == "Q2":
        return "Q1", current_fy
    elif current_q == "Q3":
        return "Q2", current_fy
    else:  # Q4
        return "Q3", current_fy

class SchedulerService:
    def __init__(self, poll_interval_seconds=600):  # Default: 10 minutes
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
        
        NOTE: Auto-analysis only triggers for the LATEST quarter (previous FY quarter).
        Other quarters are stored but require manual analysis trigger.
        """
        stock_id = stock_row['id']
        symbol = stock_row['stock_symbol'] or stock_row['bse_code']

        if not symbol:
            print(f"[Scheduler] No symbol/bse_code found for stock id {stock_id}, skipping")
            return

        print(f"[Scheduler] Checking {symbol}...")
        
        # Get the latest quarter for auto-analysis
        latest_quarter, latest_year = _get_latest_quarter()
        print(f"[Scheduler] Latest quarter for auto-analysis: {latest_quarter} FY{latest_year}")

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
                new_transcript_id = cursor.lastrowid
                
                # Only auto-trigger analysis for the LATEST quarter
                if transcript.quarter != latest_quarter or transcript.year != latest_year:
                    print(f"[Scheduler] Transcript {transcript.quarter} {transcript.year} stored but not auto-analyzed (not latest quarter)")
                else:
                    # Check if analysis already exists for this transcript (prevents duplicate emails on restart)
                    cursor.execute("""
                        SELECT id FROM transcript_analyses WHERE transcript_id = ?
                    """, (new_transcript_id,))
                    if cursor.fetchone():
                        print(f"[Scheduler] Analysis already exists for {symbol} {transcript.quarter} {transcript.year}, skipping")
                    else:
                        print(f"[Scheduler] Auto-triggering analysis for {symbol} {transcript.quarter} {transcript.year}")
                        try:
                            job_id = self.analysis_worker.start_analysis_job(stock_id, transcript.quarter, transcript.year)
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
                
                # Only auto-trigger analysis for the LATEST quarter
                if existing['quarter'] != latest_quarter or existing['year'] != latest_year:
                    print(f"[Scheduler] Transcript {existing['quarter']} {existing['year']} updated but not auto-analyzed (not latest quarter)")
                else:
                    # Check if analysis already exists for this transcript (prevents duplicate emails on restart)
                    cursor.execute("""
                        SELECT id FROM transcript_analyses WHERE transcript_id = ?
                    """, (existing['id'],))
                    if cursor.fetchone():
                        print(f"[Scheduler] Analysis already exists for {symbol} {existing['quarter']} {existing['year']}, skipping")
                    else:
                        print(f"[Scheduler] Auto-triggering analysis for {symbol} {existing['quarter']} {existing['year']}")
                        try:
                            job_id = self.analysis_worker.start_analysis_job(stock_id, existing['quarter'], existing['year'])
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
        Polls Tijori API for all stocks in the watchlist and all stocks that belong to any group.
        Updates transcripts table with new available transcripts or upcoming calls.
        """
        print(f"[Scheduler] Starting watchlist poll at {datetime.now()}")
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Collect stocks from watchlist
            cursor.execute("""
                SELECT s.id, s.stock_symbol, s.bse_code, s.isin_number 
                FROM stocks s
                INNER JOIN watchlist_items w ON s.id = w.stock_id
            """)
            watchlist_stocks = cursor.fetchall()

            # Collect stocks from groups
            cursor.execute("""
                SELECT DISTINCT s.id, s.stock_symbol, s.bse_code, s.isin_number
                FROM stocks s
                INNER JOIN group_stocks gs ON s.id = gs.stock_id
            """)
            group_stocks = cursor.fetchall()

            # Merge and de-duplicate by stock id
            unique_stocks = {}
            for stock in watchlist_stocks:
                unique_stocks[stock["id"]] = stock
            for stock in group_stocks:
                unique_stocks[stock["id"]] = stock

            stock_list = list(unique_stocks.values())
            print(f"[Scheduler] Found {len(watchlist_stocks)} stocks in watchlist, {len(group_stocks)} in groups, {len(stock_list)} unique to check")
            
            for stock in stock_list:
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
            cursor.execute("SELECT id, stock_symbol, bse_code FROM stocks WHERE id = ?", (stock_id,))
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
