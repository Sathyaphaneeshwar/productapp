"""
Configuration file for backend - PyInstaller compatible
"""
import os
import sys
from pathlib import Path

def get_base_dir():
    """Get base directory, works both in development and PyInstaller bundle"""
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller bundle
        return Path(sys._MEIPASS)
    else:
        # Running in development
        return Path(__file__).parent.parent

BASE_DIR = get_base_dir()

# Database configuration
DATABASE_DIR = BASE_DIR / "database"
DATABASE_PATH = DATABASE_DIR / "stocks.db"
SCHEMA_PATH = DATABASE_DIR / "schema.sql"

# CSV file paths
DATA_DIR = BASE_DIR / "data"
NSE_CSV_PATH = DATA_DIR / "EQUITY_L.csv"
BSE_CSV_PATH = DATA_DIR / "Equity.csv"

# Logging configuration
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "stock_updates.log"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_LEVEL = "INFO"

# Ensure directories exist (only in dev mode, not in frozen)
if not getattr(sys, 'frozen', False):
    DATABASE_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
