#!/usr/bin/env python3
"""
Database Migration Script

This script safely migrates an existing database to the latest schema.
It only ADDS missing tables and columns - it never deletes existing data.

Usage:
    python scripts/migrate_database.py
"""

import sqlite3
import os
import sys
import shutil
from datetime import datetime
from pathlib import Path

# Configuration
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATABASE_PATH = PROJECT_ROOT / "database" / "stocks.db"
SCHEMA_PATH = PROJECT_ROOT / "database" / "schema.sql"
BACKUP_DIR = PROJECT_ROOT / "database" / "backups"


def get_existing_tables(conn: sqlite3.Connection) -> set:
    """Get all existing table names in the database."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    return {row[0] for row in cursor.fetchall()}


def get_existing_columns(conn: sqlite3.Connection, table_name: str) -> set:
    """Get all column names for a given table."""
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


def get_existing_indexes(conn: sqlite3.Connection) -> set:
    """Get all existing index names in the database."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    )
    return {row[0] for row in cursor.fetchall()}


def create_backup(db_path: Path) -> Path:
    """Create a backup of the database before migration."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"stocks_backup_{timestamp}.db"
    shutil.copy2(db_path, backup_path)
    return backup_path


def parse_schema_tables(schema_sql: str) -> dict:
    """Parse CREATE TABLE statements from schema.sql."""
    tables = {}
    lines = schema_sql.split('\n')
    current_table = None
    current_columns = []
    in_table = False
    
    for line in lines:
        line = line.strip()
        
        # Detect CREATE TABLE
        if line.upper().startswith('CREATE TABLE'):
            # Extract table name
            parts = line.split()
            for i, part in enumerate(parts):
                if part.upper() in ('TABLE', 'EXISTS'):
                    continue
                if part.upper() == 'IF':
                    continue
                if part.upper() == 'NOT':
                    continue
                if '(' in part:
                    current_table = part.split('(')[0]
                    break
                elif i > 0 and parts[i-1].upper() == 'EXISTS':
                    current_table = part.rstrip('(')
                    break
            in_table = True
            current_columns = []
            
        elif in_table and current_table:
            # Parse column definitions
            if line.startswith(')'):
                tables[current_table] = current_columns
                current_table = None
                in_table = False
            elif line and not line.startswith('--'):
                # Extract column name (first word before space or type)
                col_line = line.rstrip(',')
                if not any(col_line.upper().startswith(kw) for kw in 
                          ['PRIMARY', 'FOREIGN', 'UNIQUE', 'CHECK', 'CONSTRAINT']):
                    parts = col_line.split()
                    if parts:
                        col_name = parts[0]
                        col_type = parts[1] if len(parts) > 1 else 'TEXT'
                        col_def = col_line
                        current_columns.append({
                            'name': col_name,
                            'type': col_type,
                            'definition': col_def
                        })
    
    return tables


def migrate_database():
    """Main migration function."""
    print("=" * 60)
    print("  Database Migration Script")
    print("=" * 60)
    print()
    
    # Check if database exists
    if not DATABASE_PATH.exists():
        print(f"Database not found at {DATABASE_PATH}")
        print("Creating new database from schema...")
        conn = sqlite3.connect(DATABASE_PATH)
        with open(SCHEMA_PATH, 'r') as f:
            conn.executescript(f.read())
        conn.close()
        print("✓ New database created successfully!")
        return
    
    # Check if schema exists
    if not SCHEMA_PATH.exists():
        print(f"ERROR: Schema file not found at {SCHEMA_PATH}")
        sys.exit(1)
    
    # Create backup first
    print("Step 1: Creating backup...")
    backup_path = create_backup(DATABASE_PATH)
    print(f"  ✓ Backup created: {backup_path}")
    print()
    
    # Read schema
    with open(SCHEMA_PATH, 'r') as f:
        schema_sql = f.read()
    
    # Connect to database
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    
    try:
        # Get current state
        existing_tables = get_existing_tables(conn)
        existing_indexes = get_existing_indexes(conn)
        
        print("Step 2: Analyzing current database...")
        print(f"  Existing tables: {len(existing_tables)}")
        for t in sorted(existing_tables):
            print(f"    - {t}")
        print()
        
        # Parse schema for expected tables
        schema_tables = parse_schema_tables(schema_sql)
        
        print("Step 3: Applying migrations...")
        changes_made = 0
        
        # Run the schema SQL - CREATE IF NOT EXISTS handles new tables
        # Split by statement to handle errors gracefully
        statements = schema_sql.split(';')
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt or stmt.startswith('--'):
                continue
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                # Ignore "already exists" errors, they're expected
                if 'already exists' not in str(e).lower():
                    # Check if it's a column addition error
                    if 'duplicate column' not in str(e).lower():
                        print(f"  Note: {e}")
        
        # Check for missing columns in existing tables
        for table_name, columns in schema_tables.items():
            if table_name in existing_tables:
                existing_cols = get_existing_columns(conn, table_name)
                for col in columns:
                    if col['name'] not in existing_cols:
                        # Add missing column
                        try:
                            alter_sql = f"ALTER TABLE {table_name} ADD COLUMN {col['definition']}"
                            conn.execute(alter_sql)
                            print(f"  ✓ Added column '{col['name']}' to table '{table_name}'")
                            changes_made += 1
                        except sqlite3.OperationalError as e:
                            if 'duplicate column' not in str(e).lower():
                                print(f"  Note: Could not add {col['name']}: {e}")
        
        conn.commit()
        
        # Summary
        new_tables = get_existing_tables(conn)
        tables_added = new_tables - existing_tables
        
        print()
        print("=" * 60)
        print("  Migration Complete!")
        print("=" * 60)
        print()
        
        if tables_added:
            print(f"New tables added: {len(tables_added)}")
            for t in sorted(tables_added):
                print(f"  + {t}")
        
        if changes_made > 0:
            print(f"Columns added: {changes_made}")
        
        if not tables_added and changes_made == 0:
            print("✓ Database was already up to date. No changes needed.")
        
        print()
        print(f"Total tables: {len(new_tables)}")
        print(f"Backup saved: {backup_path}")
        print()
        
    finally:
        conn.close()


if __name__ == "__main__":
    migrate_database()
