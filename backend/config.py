"""
Configuration file for backend - PyInstaller compatible
"""
import os
import sys
import shutil
import sqlite3
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
            # Windows: %APPDATA%\ProductGemini
            app_data = Path(os.environ.get('APPDATA', Path.home())) / 'ProductGemini'
        else:
            # Linux: ~/.local/share/ProductGemini
            app_data = Path.home() / '.local' / 'share' / 'ProductGemini'
        return app_data
    else:
        # Running in development - use project root
        return Path(__file__).parent.parent

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

def initialize_user_data():
    """Copy bundled database to user data directory if it doesn't exist"""
    # Create directories
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    # Copy database if it doesn't exist in user data
    if not DATABASE_PATH.exists():
        if BUNDLED_DATABASE_PATH.exists():
            print(f"[Config] Copying database to {DATABASE_PATH}")
            shutil.copy2(BUNDLED_DATABASE_PATH, DATABASE_PATH)
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
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transcripts'")
        if not cursor.fetchone():
            return

        cursor.execute("PRAGMA table_info(transcripts)")
        columns = {row[1] for row in cursor.fetchall()}

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transcript_checks'")
        transcript_checks_exists = cursor.fetchone() is not None

        missing_analysis_status = 'analysis_status' not in columns
        missing_analysis_error = 'analysis_error' not in columns
        missing_updated_at = 'updated_at' not in columns
        missing_transcript_checks = not transcript_checks_exists

        if not (missing_analysis_status or missing_analysis_error or missing_updated_at or missing_transcript_checks):
            return

        backup_dir = DATABASE_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"stocks_backup_{timestamp}.db"
        try:
            shutil.copy2(DATABASE_PATH, backup_path)
        except Exception as backup_error:
            print(f"[Config] Backup failed before migration: {backup_error}")

        if missing_analysis_status:
            cursor.execute("ALTER TABLE transcripts ADD COLUMN analysis_status TEXT")
        if missing_analysis_error:
            cursor.execute("ALTER TABLE transcripts ADD COLUMN analysis_error TEXT")
        if missing_updated_at:
            cursor.execute("ALTER TABLE transcripts ADD COLUMN updated_at TIMESTAMP")
            cursor.execute("UPDATE transcripts SET updated_at = CURRENT_TIMESTAMP")

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

        conn.commit()
    except Exception as e:
        print(f"[Config] Schema migration failed: {e}")
    finally:
        conn.close()

# Initialize on import
initialize_user_data()
ensure_schema_migrations()
