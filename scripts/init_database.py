#!/usr/bin/env python3
"""
Initialize stock database and perform initial data load from CSV files
"""
import sqlite3
import csv
import logging
from pathlib import Path
from config import (
    DATABASE_PATH, SCHEMA_PATH, NSE_CSV_PATH, BSE_CSV_PATH,
    LOG_FILE, LOG_FORMAT, LOG_LEVEL
)

# Setup logging
logging.basicConfig(
    level=LOG_LEVEL,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def create_database():
    """Create database and tables from schema file"""
    logger.info("Creating database...")
    
    try:
        # Read schema file
        with open(SCHEMA_PATH, 'r') as f:
            schema_sql = f.read()
        
        # Create database and execute schema
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.executescript(schema_sql)
        conn.commit()
        conn.close()
        
        logger.info(f"Database created successfully at {DATABASE_PATH}")
        return True
    except Exception as e:
        logger.error(f"Error creating database: {e}")
        return False


def load_nse_data(conn):
    """Load NSE stock data from EQUITY_L.csv"""
    logger.info("Loading NSE data...")
    
    inserted = 0
    skipped = 0
    errors = 0
    
    try:
        with open(NSE_CSV_PATH, 'r', encoding='utf-8') as f:
            # Read and clean column names
            reader = csv.DictReader(f)
            reader.fieldnames = [name.strip() for name in reader.fieldnames]
            cursor = conn.cursor()
            
            for row in reader:
                try:
                    isin = row['ISIN NUMBER'].strip()
                    symbol = row['SYMBOL'].strip()
                    name = row['NAME OF COMPANY'].strip()
                    
                    if not isin:
                        skipped += 1
                        continue
                    
                    cursor.execute("""
                        INSERT OR IGNORE INTO stocks (stock_symbol, isin_number, stock_name)
                        VALUES (?, ?, ?)
                    """, (symbol, isin, name))
                    
                    if cursor.rowcount > 0:
                        inserted += 1
                    else:
                        skipped += 1
                        
                except Exception as e:
                    logger.warning(f"Error processing NSE row: {e}")
                    errors += 1
            
            conn.commit()
            
    except Exception as e:
        logger.error(f"Error loading NSE data: {e}")
        return 0, 0, 0
    
    logger.info(f"NSE data loaded: {inserted} inserted, {skipped} skipped, {errors} errors")
    return inserted, skipped, errors


def load_bse_data(conn):
    """Load BSE stock data from Equity.csv"""
    logger.info("Loading BSE data...")
    
    inserted = 0
    updated = 0
    skipped = 0
    errors = 0
    
    try:
        with open(BSE_CSV_PATH, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            cursor = conn.cursor()
            
            for row in reader:
                try:
                    isin = row['ISIN No'].strip()
                    bse_code = row['Security Code'].strip()
                    name = row['Issuer Name'].strip()
                    
                    if not isin:
                        skipped += 1
                        continue
                    
                    # Check if ISIN already exists
                    cursor.execute("SELECT id, bse_code FROM stocks WHERE isin_number = ?", (isin,))
                    existing = cursor.fetchone()
                    
                    if existing:
                        # Update BSE code if not already set
                        if not existing[1]:
                            cursor.execute("""
                                UPDATE stocks SET bse_code = ?, updated_at = CURRENT_TIMESTAMP
                                WHERE isin_number = ?
                            """, (bse_code, isin))
                            updated += 1
                        else:
                            skipped += 1
                    else:
                        # Insert new stock with BSE data
                        cursor.execute("""
                            INSERT INTO stocks (bse_code, isin_number, stock_name)
                            VALUES (?, ?, ?)
                        """, (bse_code, isin, name))
                        inserted += 1
                        
                except Exception as e:
                    logger.warning(f"Error processing BSE row: {e}")
                    errors += 1
            
            conn.commit()
            
    except Exception as e:
        logger.error(f"Error loading BSE data: {e}")
        return 0, 0, 0, 0
    
    logger.info(f"BSE data loaded: {inserted} inserted, {updated} updated, {skipped} skipped, {errors} errors")
    return inserted, updated, skipped, errors


def main():
    """Main function to initialize database"""
    logger.info("=" * 60)
    logger.info("Starting database initialization")
    logger.info("=" * 60)
    
    # Create database
    if not create_database():
        logger.error("Failed to create database. Exiting.")
        return
    
    # Connect to database
    conn = sqlite3.connect(DATABASE_PATH)
    
    try:
        # Load NSE data first
        nse_inserted, nse_skipped, nse_errors = load_nse_data(conn)
        
        # Load BSE data (will update NSE records with BSE codes or insert new ones)
        bse_inserted, bse_updated, bse_skipped, bse_errors = load_bse_data(conn)
        
        # Get final count
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM stocks")
        total_stocks = cursor.fetchone()[0]
        
        logger.info("=" * 60)
        logger.info("Database initialization complete!")
        logger.info(f"Total stocks in database: {total_stocks}")
        logger.info(f"NSE: {nse_inserted} inserted, {nse_skipped} skipped, {nse_errors} errors")
        logger.info(f"BSE: {bse_inserted} inserted, {bse_updated} updated, {bse_skipped} skipped, {bse_errors} errors")
        logger.info("=" * 60)
        
    finally:
        conn.close()


if __name__ == "__main__":
    main()
