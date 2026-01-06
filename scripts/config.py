"""
Configuration file for stock database system
"""
import os
from pathlib import Path

# Base directory (project root)
BASE_DIR = Path(__file__).parent.parent

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

# Ensure directories exist
DATABASE_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
