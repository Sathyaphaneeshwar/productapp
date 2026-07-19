"""
Configuration file for backend - PyInstaller compatible
"""
import os
import sys
import shutil
import sqlite3
from typing import Optional
from datetime import datetime
from pathlib import Path

def get_base_dir():
    """Get base directory for bundled resources (read-only in frozen mode)"""
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller bundle - this is READ-ONLY
        return Path(sys._MEIPASS)
    else:
        # Running in development
        return Path(__file__).parent.parent

def get_user_data_dir():
    """Get user-writable data directory for database and logs"""
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller bundle - use user's app data folder
        if sys.platform == 'darwin':
            # macOS: ~/Library/Application Support/ProductGemini
            app_data = Path.home() / 'Library' / 'Application Support' / 'ProductGemini'
        elif sys.platform == 'win32':
            # Windows: keep the WAL database out of roaming-profile sync.
            # Existing %APPDATA% databases are migrated by _legacy_db_candidates.
            windows_base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
            app_data = Path(windows_base or Path.home()) / 'ProductGemini'
        else:
            # Linux: ~/.local/share/ProductGemini
            app_data = Path.home() / '.local' / 'share' / 'ProductGemini'
        return app_data
    else:
        # Keep developer/user state separate from the tracked, sanitized seed
        # database that is bundled into installers.
        return Path(__file__).parent.parent / '.local-data'

BASE_DIR = get_base_dir()
USER_DATA_DIR = get_user_data_dir()

# Bundled resources (read-only in frozen mode)
BUNDLED_DATABASE_DIR = BASE_DIR / "database"
BUNDLED_DATABASE_PATH = BUNDLED_DATABASE_DIR / "stocks.db"
BUNDLED_SCHEMA_PATH = BUNDLED_DATABASE_DIR / "schema.sql"

# User data (writable) - this is where the actual database lives
DATABASE_DIR = USER_DATA_DIR / "database"
DATABASE_PATH = DATABASE_DIR / "stocks.db"
SCHEMA_PATH = DATABASE_DIR / "schema.sql"

# CSV file paths (read from bundle, these are read-only which is fine)
DATA_DIR = BASE_DIR / "data"
NSE_CSV_PATH = DATA_DIR / "EQUITY_L.csv"
BSE_CSV_PATH = DATA_DIR / "Equity.csv"

# Logging configuration
LOG_DIR = USER_DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_LEVEL = "INFO"

def _looks_like_database(db_path: Path) -> bool:
    try:
        if not db_path.exists():
            return False
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stocks'")
        ok = cursor.fetchone() is not None
        conn.close()
        return ok
    except Exception:
        return False

def _legacy_db_candidates() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []

    if sys.platform == 'darwin':
        base = home / 'Library' / 'Application Support'
        names = [
            'ProductGemini',
            'Product Gemini',
            'ProductGemini Desktop',
            'ProductGeminiApp',
            'StockDiscovery',
            'stockapp',
        ]
        for name in names:
            candidates.append(base / name / 'database' / 'stocks.db')
    elif sys.platform == 'win32':
        roaming_base = Path(os.environ.get('APPDATA', home))
        local_base = Path(
            os.environ.get('LOCALAPPDATA')
            or os.environ.get('APPDATA')
            or home
        )
        names = [
            'ProductGemini',
            'Product Gemini',
            'ProductGemini Desktop',
            'ProductGeminiApp',
            'StockDiscovery',
            'stockapp',
        ]
        # Roaming comes first so the pre-v1.2.27 ProductGemini database is
        # found during the one-time move to %LOCALAPPDATA%.
        for base in (roaming_base, local_base):
            for name in names:
                candidates.append(base / name / 'database' / 'stocks.db')
    else:
        base = home / '.local' / 'share'
        names = [
            'ProductGemini',
            'Product Gemini',
            'ProductGemini Desktop',
            'ProductGeminiApp',
            'StockDiscovery',
            'stockapp',
        ]
        for name in names:
            candidates.append(base / name / 'database' / 'stocks.db')

    return candidates

def _find_legacy_db() -> Optional[Path]:
    candidates = [p for p in _legacy_db_candidates() if p.exists()]
    valid = [p for p in candidates if _looks_like_database(p)]
    if not valid:
        return None
    valid.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return valid[0]


def _copy_sqlite_database(source: Path, destination: Path):
    """Copy a SQLite database through its backup API so WAL data is included."""
    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    destination_conn = sqlite3.connect(destination, timeout=30)
    try:
        source_conn.backup(destination_conn)
    finally:
        destination_conn.close()
        source_conn.close()


def _clear_seeded_data():
    try:
        if not DATABASE_PATH.exists():
            return
        conn = sqlite3.connect(DATABASE_PATH)
        conn.execute("PRAGMA foreign_keys = OFF")
        cursor = conn.cursor()
        private_tables = (
            "queue_messages",
            "email_outbox",
            "analysis_jobs",
            "stock_activity_logs",
            "transcript_events",
            "transcript_checks",
            "transcript_fetch_schedule",
            "transcript_analyses",
            "transcripts",
            "group_research_runs",
            "document_research_runs",
            "watchlist_items",
            "group_stocks",
            "groups",
            "email_list",
            "smtp_settings",
            "api_keys",
            "llm_settings",
        )
        for table_name in private_tables:
            cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            if cursor.fetchone():
                cursor.execute(f'DELETE FROM "{table_name}"')

        cursor.execute("PRAGMA table_info(llm_providers)")
        provider_columns = {row[1] for row in cursor.fetchall()}
        for column_name in provider_columns & {"api_key", "api_key_encrypted"}:
            cursor.execute(f'UPDATE llm_providers SET "{column_name}" = NULL')
        conn.commit()
        conn.close()
        print("[Config] Cleared user state from bundled seed database")
    except Exception as e:
        print(f"[Config] Seed cleanup failed: {e}")

def initialize_user_data():
    """Copy bundled database to user data directory if it doesn't exist"""
    # Create directories
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    # Copy database if it doesn't exist in user data
    if not DATABASE_PATH.exists():
        legacy_db = _find_legacy_db()
        if legacy_db:
            print(f"[Config] Migrating legacy database from {legacy_db}")
            _copy_sqlite_database(legacy_db, DATABASE_PATH)
        elif BUNDLED_DATABASE_PATH.exists():
            print(f"[Config] Copying database to {DATABASE_PATH}")
            shutil.copy2(BUNDLED_DATABASE_PATH, DATABASE_PATH)
            _clear_seeded_data()
        else:
            print(f"[Config] WARNING: Bundled database not found at {BUNDLED_DATABASE_PATH}")
    
    # Copy schema if it doesn't exist
    if not SCHEMA_PATH.exists():
        if BUNDLED_SCHEMA_PATH.exists():
            shutil.copy2(BUNDLED_SCHEMA_PATH, SCHEMA_PATH)

_migrations_ran = False

def ensure_schema_migrations():
    """Apply additive schema updates for existing user databases."""
    global _migrations_ran
    if _migrations_ran:
        return
    _migrations_ran = True

    if not DATABASE_PATH.exists():
        return

    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transcripts'")
        if not cursor.fetchone():
            return

        cursor.execute("PRAGMA table_info(transcripts)")
        columns = {row[1] for row in cursor.fetchall()}
        cursor.execute("PRAGMA table_info(transcript_analyses)")
        analysis_columns = {row[1] for row in cursor.fetchall()}
        cursor.execute("PRAGMA table_info(llm_providers)")
        llm_provider_columns = {row[1] for row in cursor.fetchall()}
        cursor.execute("PRAGMA table_info(smtp_settings)")
        smtp_columns = {row[1] for row in cursor.fetchall()}
        cursor.execute("PRAGMA table_info(stocks)")
        stock_columns = {row[1] for row in cursor.fetchall()}

        def table_exists(name: str) -> bool:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
            return cursor.fetchone() is not None

        transcript_checks_exists = table_exists("transcript_checks")

        missing_analysis_status = 'analysis_status' not in columns
        missing_analysis_error = 'analysis_error' not in columns
        missing_updated_at = 'updated_at' not in columns
        missing_transcript_checks = not transcript_checks_exists
        missing_analysis_model_name = 'model_name' not in analysis_columns
        missing_llm_api_key = 'api_key' not in llm_provider_columns

        missing_fetch_schedule = not table_exists("transcript_fetch_schedule")
        missing_transcript_events = not table_exists("transcript_events")
        missing_analysis_jobs = not table_exists("analysis_jobs")
        missing_queue_messages = not table_exists("queue_messages")
        missing_email_outbox = not table_exists("email_outbox")
        missing_stock_activity_logs = not table_exists("stock_activity_logs")
        missing_smtp_security = 'smtp_security' not in smtp_columns
        missing_stock_source = 'source' not in stock_columns
        missing_stock_extra_code = 'extra_code' not in stock_columns
        missing_stock_is_active = 'is_active' not in stock_columns
        missing_stock_import_batches = not table_exists("stock_import_batches")

        queue_index_missing = False
        queue_claim_columns_missing = False
        if not missing_queue_messages:
            cursor.execute("PRAGMA table_info(queue_messages)")
            queue_columns = {row[1] for row in cursor.fetchall()}
            required_queue_columns = {
                "status",
                "locked_until",
                "worker_id",
                "delivery_attempts",
                "last_error",
                "dedupe_key",
                "updated_at",
            }
            queue_claim_columns_missing = not required_queue_columns.issubset(queue_columns)
            cursor.execute("PRAGMA index_list(queue_messages)")
            queue_indexes = {row[1] for row in cursor.fetchall()}
            queue_index_missing = not {
                "idx_queue_messages_due",
                "idx_queue_messages_dedupe",
                "idx_queue_messages_claim",
            }.issubset(queue_indexes)

        needs_migration = (
            missing_analysis_status
            or missing_analysis_error
            or missing_updated_at
            or missing_transcript_checks
            or missing_analysis_model_name
            or missing_llm_api_key
            or missing_fetch_schedule
            or missing_transcript_events
            or missing_analysis_jobs
            or missing_queue_messages
            or queue_claim_columns_missing
            or queue_index_missing
            or missing_email_outbox
            or missing_stock_activity_logs
            or missing_smtp_security
            or missing_stock_source
            or missing_stock_extra_code
            or missing_stock_is_active
            or missing_stock_import_batches
        )

        if not needs_migration:
            return

        backup_dir = DATABASE_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"stocks_backup_{timestamp}.db"
        try:
            _copy_sqlite_database(DATABASE_PATH, backup_path)
        except Exception as backup_error:
            print(f"[Config] Backup failed before migration: {backup_error}")

        if missing_analysis_status:
            cursor.execute("ALTER TABLE transcripts ADD COLUMN analysis_status TEXT")
        if missing_analysis_error:
            cursor.execute("ALTER TABLE transcripts ADD COLUMN analysis_error TEXT")
        if missing_updated_at:
            cursor.execute("ALTER TABLE transcripts ADD COLUMN updated_at TIMESTAMP")
            cursor.execute("UPDATE transcripts SET updated_at = CURRENT_TIMESTAMP")
        if missing_analysis_model_name:
            cursor.execute("ALTER TABLE transcript_analyses ADD COLUMN model_name TEXT")
        if missing_llm_api_key:
            cursor.execute("ALTER TABLE llm_providers ADD COLUMN api_key TEXT")
            if 'api_key_encrypted' in llm_provider_columns:
                cursor.execute("""
                    UPDATE llm_providers
                    SET api_key = api_key_encrypted
                    WHERE api_key IS NULL
                      AND api_key_encrypted IS NOT NULL
                      AND api_key_encrypted != ''
                """)
        if missing_smtp_security:
            cursor.execute(
                "ALTER TABLE smtp_settings ADD COLUMN smtp_security TEXT NOT NULL DEFAULT 'auto'"
            )
        if missing_stock_source:
            cursor.execute(
                "ALTER TABLE stocks ADD COLUMN source TEXT NOT NULL DEFAULT 'master'"
            )
        if missing_stock_extra_code:
            cursor.execute("ALTER TABLE stocks ADD COLUMN extra_code TEXT")
        if missing_stock_is_active:
            cursor.execute(
                "ALTER TABLE stocks ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_stocks_source_active
            ON stocks(source, is_active)
            """
        )

        if missing_stock_import_batches:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_import_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    uploaded_ts INTEGER NOT NULL,
                    rows_total INTEGER NOT NULL DEFAULT 0,
                    rows_new INTEGER NOT NULL DEFAULT 0,
                    rows_updated INTEGER NOT NULL DEFAULT 0,
                    rows_unchanged INTEGER NOT NULL DEFAULT 0,
                    rows_invalid INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'previewed',
                    payload_json TEXT NOT NULL,
                    warnings_json TEXT,
                    committed_ts INTEGER
                )
                """
            )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_stock_import_batches_status_time
            ON stock_import_batches(status, uploaded_ts)
            """
        )
        cursor.execute(
            """
            DELETE FROM stock_import_batches
            WHERE status = 'previewed'
              AND uploaded_ts < CAST(strftime('%s', 'now') AS INTEGER) - 86400
            """
        )

        if missing_transcript_checks:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transcript_checks (
                    stock_id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'idle',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_transcript_checks_status ON transcript_checks(status)
            """)

        if missing_fetch_schedule:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transcript_fetch_schedule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_id INTEGER NOT NULL,
                    quarter TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    next_check_at TIMESTAMP,
                    last_status TEXT,
                    last_checked_at TIMESTAMP,
                    last_available_at TIMESTAMP,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(stock_id, quarter, year),
                    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fetch_schedule_next ON transcript_fetch_schedule(next_check_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fetch_schedule_priority ON transcript_fetch_schedule(priority)")

        if missing_transcript_events:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transcript_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_id INTEGER NOT NULL,
                    quarter TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    source_url TEXT,
                    event_date TIMESTAMP,
                    observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    origin TEXT,
                    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_transcript_events_stock ON transcript_events(stock_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_transcript_events_quarter ON transcript_events(quarter, year)")

        if missing_analysis_jobs:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS analysis_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transcript_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT NOT NULL,
                    force INTEGER NOT NULL DEFAULT 0,
                    retry_next_at TIMESTAMP,
                    locked_until TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(idempotency_key),
                    FOREIGN KEY (transcript_id) REFERENCES transcripts(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_jobs_status ON analysis_jobs(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_jobs_retry ON analysis_jobs(retry_next_at)")
        else:
            cursor.execute("PRAGMA table_info(analysis_jobs)")
            analysis_job_columns = {row[1] for row in cursor.fetchall()}
            if 'force' not in analysis_job_columns:
                cursor.execute("ALTER TABLE analysis_jobs ADD COLUMN force INTEGER NOT NULL DEFAULT 0")

        if missing_queue_messages:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS queue_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    queue_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL DEFAULT 'pending',
                    locked_until TIMESTAMP,
                    worker_id TEXT,
                    delivery_attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    dedupe_key TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            cursor.execute("PRAGMA table_info(queue_messages)")
            queue_columns = {row[1] for row in cursor.fetchall()}
            queue_column_definitions = {
                "status": "TEXT NOT NULL DEFAULT 'pending'",
                "locked_until": "TIMESTAMP",
                "worker_id": "TEXT",
                "delivery_attempts": "INTEGER NOT NULL DEFAULT 0",
                "last_error": "TEXT",
                "dedupe_key": "TEXT",
                "updated_at": "TIMESTAMP",
            }
            for column_name, column_definition in queue_column_definitions.items():
                if column_name not in queue_columns:
                    cursor.execute(
                        f"ALTER TABLE queue_messages ADD COLUMN {column_name} {column_definition}"
                    )
            cursor.execute(
                """
                UPDATE queue_messages
                SET status = COALESCE(status, 'pending'),
                    delivery_attempts = COALESCE(delivery_attempts, 0),
                    updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
                """
            )

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_messages_due
            ON queue_messages(queue_name, available_at, id)
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_messages_dedupe
            ON queue_messages(queue_name, dedupe_key)
            WHERE dedupe_key IS NOT NULL
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_messages_claim
            ON queue_messages(queue_name, status, locked_until, available_at)
        """)

        if missing_email_outbox:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS email_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_id INTEGER NOT NULL,
                    recipient TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    scheduled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    retry_next_at TIMESTAMP,
                    locked_until TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(analysis_id, recipient),
                    FOREIGN KEY (analysis_id) REFERENCES transcript_analyses(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_outbox_status ON email_outbox(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_outbox_retry ON email_outbox(retry_next_at)")

        if missing_stock_activity_logs:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock_activity_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_id INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    level TEXT NOT NULL CHECK(level IN ('info', 'success', 'error')),
                    message TEXT NOT NULL,
                    quarter TEXT,
                    year INTEGER,
                    details_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_stock_activity_stock_time
                ON stock_activity_logs(stock_id, created_at DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_stock_activity_level_time
                ON stock_activity_logs(level, created_at DESC)
            """)

        conn.commit()
    except Exception as e:
        print(f"[Config] Schema migration failed: {e}")
    finally:
        conn.close()

def ensure_data_migrations():
    """Apply data fixes for existing user databases."""
    if not DATABASE_PATH.exists():
        return

    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()
    try:
        # Data fix: Correct ISIN for stocks where Tijori API uses a different ISIN
        # These are known mismatches between our bundled data and Tijori's database
        isin_corrections = [
            # Kotak Bank: Our DB had INE237A01028, Tijori uses INE237A01036
            ('KOTAKBANK', 'INE237A01028', 'INE237A01036'),
        ]
        
        for symbol, wrong_isin, correct_isin in isin_corrections:
            cursor.execute("""
                UPDATE stocks 
                SET isin_number = ? 
                WHERE stock_symbol = ? AND isin_number = ?
            """, (correct_isin, symbol, wrong_isin))
            if cursor.rowcount > 0:
                print(f"[Config] Fixed ISIN for {symbol}: {wrong_isin} -> {correct_isin}")
        
        # Cleanup orphaned rows (idempotent)
        cursor.execute("""
            DELETE FROM group_stocks
            WHERE group_id NOT IN (SELECT id FROM groups)
               OR stock_id NOT IN (SELECT id FROM stocks)
        """)
        cursor.execute("""
            DELETE FROM watchlist_items
            WHERE stock_id NOT IN (SELECT id FROM stocks)
        """)
        cursor.execute("""
            DELETE FROM transcript_checks
            WHERE stock_id NOT IN (SELECT id FROM stocks)
        """)
        cursor.execute("""
            DELETE FROM transcripts
            WHERE stock_id NOT IN (SELECT id FROM stocks)
        """)
        cursor.execute("""
            DELETE FROM transcript_analyses
            WHERE transcript_id NOT IN (SELECT id FROM transcripts)
        """)

        # Reconcile: if a transcript is available for a quarter, mark upcoming rows as available.
        cursor.execute("""
            UPDATE transcripts
            SET status = 'available',
                source_url = COALESCE(
                    source_url,
                    (SELECT t2.source_url FROM transcripts t2
                     WHERE t2.stock_id = transcripts.stock_id
                       AND t2.quarter = transcripts.quarter
                       AND t2.year = transcripts.year
                       AND t2.status = 'available'
                       AND t2.source_url IS NOT NULL
                     LIMIT 1)
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'upcoming'
              AND EXISTS (
                  SELECT 1 FROM transcripts t2
                  WHERE t2.stock_id = transcripts.stock_id
                    AND t2.quarter = transcripts.quarter
                    AND t2.year = transcripts.year
                    AND t2.status = 'available'
              )
        """)

        conn.commit()
    except Exception as e:
        print(f"[Config] Data migration failed: {e}")
    finally:
        conn.close()

# Initialize on import
initialize_user_data()
ensure_schema_migrations()
ensure_data_migrations()
