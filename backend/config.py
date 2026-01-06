"""
Configuration file for backend - PyInstaller compatible
"""
import os
import sys
import shutil
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

# Initialize on import
initialize_user_data()

