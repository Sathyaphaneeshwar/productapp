import threading
import time
import sqlite3
import os
import sys
from datetime import datetime, timedelta

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
    def __init__(self, poll_interval_seconds=300):  # Default: 5 minutes
        self.poll_interval = poll_interval_seconds
        self.transcript_service = TranscriptService()
        self.analysis_worker = AnalysisWorker()
        self.group_research_service = GroupResearchService()
        self.running = False
        self.is_polling = False
        self.last_poll_started_at = None
        self.last_poll_completed_at = None
        self.next_poll_at = datetime.now()
        self.poll_lock = threading.Lock()
        self.status_lock = threading.Lock()
        self.thread = None
        self.ensure_transcript_checks_table()

    def get_db_connection(self):
        conn = sqlite3.connect(str(DATABASE_PATH))
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_transcript_checks_table(self):
        conn = self.get_db_connection()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS transcript_checks (
                    stock_id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'idle',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_transcript_checks_status ON transcript_checks(status);
            """)
            conn.commit()
        finally:
            conn.close()

    def _update_check_status(self, cursor, stock_id: int, status: str):
        cursor.execute("""
            INSERT INTO transcript_checks (stock_id, status, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(stock_id) DO UPDATE SET
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP
        """, (stock_id, status))

    def _update_bulk_check_status(self, cursor, stock_ids: list[int], status: str):
        if not stock_ids:
            return
        rows = [(stock_id, status) for stock_id in stock_ids]
        cursor.executemany("""
            INSERT INTO transcript_checks (stock_id, status, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(stock_id) DO UPDATE SET
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP
        """, rows)

    def _set_poll_status(self, *, is_polling: bool = None, started_at: datetime = None, completed_at: datetime = None, next_poll_at: datetime = None):
        with self.status_lock:
            if is_polling is not None:
                self.is_polling = is_polling
            if started_at is not None:
                self.last_poll_started_at = started_at
            if completed_at is not None:
                self.last_poll_completed_at = completed_at
            if next_poll_at is not None:
                self.next_poll_at = next_poll_at

    def _run_poll_cycle_locked(self):
        started_at = datetime.now()
        next_poll_at = started_at + timedelta(seconds=self.poll_interval)
        self._set_poll_status(
            is_polling=True,
            started_at=started_at,
            next_poll_at=next_poll_at,
        )

        try:
            self.poll_watchlist()
            self.group_research_service.check_and_trigger_runs()
        except Exception as e:
            print(f"[Scheduler] Unexpected error: {e}")
        finally:
            completed_at = datetime.now()
            fallback_next = completed_at + timedelta(seconds=self.poll_interval)
            if fallback_next > next_poll_at:
                next_poll_at = fallback_next
            self._set_poll_status(
                is_polling=False,
                completed_at=completed_at,
                next_poll_at=next_poll_at,
            )
            try:
                self.poll_lock.release()
            except RuntimeError:
                pass

    def trigger_poll(self) -> bool:
        if not self.poll_lock.acquire(blocking=False):
            return False
        threading.Thread(target=self._run_poll_cycle_locked, daemon=True).start()
        return True

    def get_poll_status(self) -> dict:
        now = datetime.now()
        with self.status_lock:
            next_poll_at = self.next_poll_at
            is_polling = self.is_polling
            last_poll_started_at = self.last_poll_started_at
            last_poll_completed_at = self.last_poll_completed_at
            scheduler_running = self.running

        next_in = None
        next_at_iso = None
        if next_poll_at:
            next_at_iso = next_poll_at.isoformat()
            next_in = max(0, int((next_poll_at - now).total_seconds()))

        return {
            'scheduler_running': scheduler_running,
            'is_polling': is_polling,
            'poll_interval_seconds': self.poll_interval,
            'last_poll_started_at': last_poll_started_at.isoformat() if last_poll_started_at else None,
            'last_poll_completed_at': last_poll_completed_at.isoformat() if last_poll_completed_at else None,
            'next_poll_at': next_at_iso,
            'next_poll_in_seconds': next_in,
        }

    def _process_stock(self, cursor, conn, stock_row, track_status: bool = True):
        """
        Handles transcript availability/upcoming checks for a single stock.
        Shared by the scheduler loop and one-off triggers.
        
        NOTE: Auto-analysis only triggers for the LATEST quarter (previous FY quarter).
        Other quarters are stored but require manual analysis trigger.
        """
        stock_id = stock_row['id']
        symbol = stock_row['stock_symbol'] or stock_row['bse_code']

        try:
            if track_status:
                try:
                    self._update_check_status(cursor, stock_id, 'checking')
                    conn.commit()
                except Exception as e:
                    print(f"[Scheduler] Failed to set check status for stock {stock_id}: {e}")

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
                    SELECT id, status, quarter, year, source_url FROM transcripts 
                    WHERE stock_id = ? AND source_url = ?
                """, (stock_id, transcript.source_url))
                
                existing = cursor.fetchone()
                
                # Fallback: check by quarter/year if not found by URL
                # This handles cases where the transcript URL changed when it became available
                if not existing:
                    cursor.execute("""
                        SELECT id, status, quarter, year, source_url FROM transcripts 
                        WHERE stock_id = ? AND quarter = ? AND year = ?
                        LIMIT 1
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
                else:
                    if existing['status'] != 'available' or existing['source_url'] != transcript.source_url:
                        print(f"[Scheduler] Transcript now available for {symbol}: {transcript.title}")
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
        finally:
            if track_status:
                try:
                    self._update_check_status(cursor, stock_id, 'idle')
                    conn.commit()
                except Exception as e:
                    print(f"[Scheduler] Failed to update check status for stock {stock_id}: {e}")

    def poll_watchlist(self):
        """
        Polls Tijori API for all stocks in the watchlist and all stocks that belong to any group.
        Updates transcripts table with new available transcripts or upcoming calls.
        """
        print(f"[Scheduler] Starting watchlist poll at {datetime.now()}")
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        stock_ids = []
        
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
            stock_ids = [stock["id"] for stock in stock_list]

            try:
                self._update_bulk_check_status(cursor, stock_ids, 'checking')
                conn.commit()
            except Exception as e:
                print(f"[Scheduler] Failed to set bulk check status: {e}")

            for stock in stock_list:
                self._process_stock(cursor, conn, stock, track_status=False)
                    
            print(f"[Scheduler] Poll completed at {datetime.now()}")
            
        except Exception as e:
            print(f"[Scheduler] Error during poll: {e}")
        finally:
            if stock_ids:
                try:
                    self._update_bulk_check_status(cursor, stock_ids, 'idle')
                    conn.commit()
                except Exception as e:
                    print(f"[Scheduler] Failed to clear bulk check status: {e}")
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
            now = datetime.now()
            with self.status_lock:
                next_poll_at = self.next_poll_at
                is_polling = self.is_polling

            if not is_polling and next_poll_at and now >= next_poll_at:
                if self.poll_lock.acquire(blocking=False):
                    self._run_poll_cycle_locked()

            time.sleep(1)

    def start(self):
        """Starts the background scheduler."""
        if self.running:
            print("[Scheduler] Already running")
            return
        
        print(f"[Scheduler] Starting with {self.poll_interval}s interval")
        self.running = True
        self._set_poll_status(next_poll_at=datetime.now())
        self.thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.thread.start()

    def stop(self):
        """Stops the background scheduler."""
        print("[Scheduler] Stopping...")
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
