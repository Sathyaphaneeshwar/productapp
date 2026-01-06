#!/usr/bin/env python3
"""
Daily update script to add new stocks from CSV files
Only adds stocks that don't already exist in the database (based on ISIN)
"""
import sqlite3
import csv
import logging
from datetime import datetime
from config import (
    DATABASE_PATH, NSE_CSV_PATH, BSE_CSV_PATH,
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


def get_existing_isins(conn):
    """Get set of all existing ISIN numbers in database"""
    cursor = conn.cursor()
    cursor.execute("SELECT isin_number FROM stocks")
    return set(row[0] for row in cursor.fetchall())


def update_from_nse(conn, existing_isins):
    """Add new stocks from NSE CSV"""
    logger.info("Checking NSE data for new stocks...")
    
    new_stocks = 0
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
                        continue
                    
                    # Only insert if ISIN doesn't exist
                    if isin not in existing_isins:
                        cursor.execute("""
                            INSERT INTO stocks (stock_symbol, isin_number, stock_name)
                            VALUES (?, ?, ?)
                        """, (symbol, isin, name))
                        new_stocks += 1
                        existing_isins.add(isin)
                        logger.info(f"Added new NSE stock: {symbol} - {name}")
                        
                except Exception as e:
                    logger.warning(f"Error processing NSE row: {e}")
                    errors += 1
            
            conn.commit()
            
    except Exception as e:
        logger.error(f"Error reading NSE data: {e}")
        return 0, 0
    
    logger.info(f"NSE update: {new_stocks} new stocks added, {errors} errors")
    return new_stocks, errors


def update_from_bse(conn, existing_isins):
    """Add new stocks from BSE CSV or update BSE codes and symbols"""
    logger.info("Checking BSE data for new stocks...")
    
    new_stocks = 0
    updated = 0
    errors = 0
    
    try:
        with open(BSE_CSV_PATH, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            cursor = conn.cursor()
            
            for row in reader:
                try:
                    isin = row['ISIN No'].strip()
                    bse_code = row['Security Code'].strip()
                    bse_symbol = row['Security Id'].strip()  # BSE stock symbol (e.g., DECNGOLD)
                    name = row['Issuer Name'].strip()
                    
                    if not isin:
                        continue
                    
                    if isin in existing_isins:
                        # Check if we need to update BSE code or symbol
                        cursor.execute("SELECT bse_code, stock_symbol FROM stocks WHERE isin_number = ?", (isin,))
                        result = cursor.fetchone()
                        
                        if result:
                            needs_update = False
                            update_fields = []
                            update_values = []
                            
                            # Update BSE code if missing
                            if not result[0]:
                                update_fields.append("bse_code = ?")
                                update_values.append(bse_code)
                                needs_update = True
                            
                            # Update stock_symbol if missing (BSE-only stock)
                            if not result[1] and bse_symbol:
                                update_fields.append("stock_symbol = ?")
                                update_values.append(bse_symbol)
                                needs_update = True
                            
                            if needs_update:
                                update_values.append(isin)
                                cursor.execute(f"""
                                    UPDATE stocks SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP
                                    WHERE isin_number = ?
                                """, tuple(update_values))
                                updated += 1
                    else:
                        # New stock - insert it with BSE symbol in stock_symbol field
                        cursor.execute("""
                            INSERT INTO stocks (stock_symbol, bse_code, isin_number, stock_name)
                            VALUES (?, ?, ?, ?)
                        """, (bse_symbol, bse_code, isin, name))
                        new_stocks += 1
                        existing_isins.add(isin)
                        logger.info(f"Added new BSE stock: {bse_symbol} ({bse_code}) - {name}")
                        
                except Exception as e:
                    logger.warning(f"Error processing BSE row: {e}")
                    errors += 1
            
            conn.commit()
            
    except Exception as e:
        logger.error(f"Error reading BSE data: {e}")
        return 0, 0, 0
    
    logger.info(f"BSE update: {new_stocks} new stocks added, {updated} stocks updated, {errors} errors")
    return new_stocks, updated, errors


def main():
    """Main function to update database with new stocks"""
    logger.info("=" * 60)
    logger.info(f"Starting daily stock update - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    try:
        # Connect to database
        conn = sqlite3.connect(DATABASE_PATH)
        
        # Get existing ISINs
        existing_isins = get_existing_isins(conn)
        initial_count = len(existing_isins)
        logger.info(f"Current stocks in database: {initial_count}")
        
        # Update from NSE
        nse_new, nse_errors = update_from_nse(conn, existing_isins)
        
        # Update from BSE
        bse_new, bse_updated, bse_errors = update_from_bse(conn, existing_isins)
        
        # Get final count
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM stocks")
        final_count = cursor.fetchone()[0]
        
        total_new = nse_new + bse_new
        
        logger.info("=" * 60)
        logger.info("Daily update complete!")
        logger.info(f"Stocks before update: {initial_count}")
        logger.info(f"Stocks after update: {final_count}")
        logger.info(f"New stocks added: {total_new} (NSE: {nse_new}, BSE: {bse_new})")
        logger.info(f"BSE codes updated: {bse_updated}")
        logger.info(f"Total errors: {nse_errors + bse_errors}")
        logger.info("=" * 60)
        
        conn.close()
        
    except Exception as e:
        logger.error(f"Error during update: {e}")
        raise


if __name__ == "__main__":
    main()
