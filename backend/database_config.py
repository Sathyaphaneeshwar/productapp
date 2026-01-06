"""
Database Configuration Module

Provides unified database access that works with:
- SQLite (local development)
- PostgreSQL (Render.com deployment)

Usage:
    from database_config import get_db_connection, init_database
"""

import os
import sqlite3
from pathlib import Path

# Check if we're on Render (has DATABASE_URL env var)
DATABASE_URL = os.environ.get('DATABASE_URL')
IS_PRODUCTION = DATABASE_URL is not None

if IS_PRODUCTION:
    import psycopg2
    from psycopg2.extras import RealDictCursor

# Local SQLite path
LOCAL_DB_PATH = Path(__file__).parent.parent / 'database' / 'stocks.db'


def get_db_connection():
    """
    Returns a database connection based on environment.
    - Local: SQLite connection
    - Production: PostgreSQL connection
    """
    if IS_PRODUCTION:
        # PostgreSQL on Render
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    else:
        # SQLite locally
        conn = sqlite3.connect(str(LOCAL_DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn


def execute_query(query, params=None, fetch_one=False, fetch_all=False, commit=False):
    """
    Execute a query with proper handling for both SQLite and PostgreSQL.
    
    Args:
        query: SQL query string (use %s for params, works with both DBs)
        params: Query parameters tuple
        fetch_one: Return single result
        fetch_all: Return all results
        commit: Commit the transaction
    
    Returns:
        Query results or None
    """
    conn = get_db_connection()
    
    try:
        if IS_PRODUCTION:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
        else:
            cursor = conn.cursor()
        
        # Convert PostgreSQL %s placeholders to SQLite ? if needed
        if not IS_PRODUCTION and '%s' in query:
            query = query.replace('%s', '?')
        
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        result = None
        if fetch_one:
            row = cursor.fetchone()
            if IS_PRODUCTION:
                result = dict(row) if row else None
            else:
                result = dict(row) if row else None
        elif fetch_all:
            rows = cursor.fetchall()
            if IS_PRODUCTION:
                result = [dict(row) for row in rows]
            else:
                result = [dict(row) for row in rows]
        
        if commit:
            conn.commit()
            if not fetch_one and not fetch_all:
                result = cursor.lastrowid if not IS_PRODUCTION else cursor.fetchone()
        
        return result
        
    finally:
        conn.close()


def init_database():
    """
    Initialize the database schema.
    Reads from schema.sql and executes it.
    """
    schema_path = Path(__file__).parent.parent / 'database' / 'schema.sql'
    
    if not schema_path.exists():
        print(f"Schema file not found: {schema_path}")
        return False
    
    with open(schema_path, 'r') as f:
        schema_sql = f.read()
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        if IS_PRODUCTION:
            # PostgreSQL: Need to convert SQLite syntax
            # Replace SQLite-specific syntax
            pg_schema = schema_sql
            pg_schema = pg_schema.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY')
            pg_schema = pg_schema.replace('BOOLEAN DEFAULT 1', 'BOOLEAN DEFAULT TRUE')
            pg_schema = pg_schema.replace('BOOLEAN DEFAULT 0', 'BOOLEAN DEFAULT FALSE')
            pg_schema = pg_schema.replace('CURRENT_TIMESTAMP', 'NOW()')
            
            # Execute statement by statement
            for statement in pg_schema.split(';'):
                statement = statement.strip()
                if statement and not statement.startswith('--'):
                    # Skip SQLite-specific triggers
                    if 'CREATE TRIGGER' in statement.upper():
                        continue
                    try:
                        cursor.execute(statement)
                    except Exception as e:
                        print(f"Schema warning: {e}")
        else:
            # SQLite: Execute as-is
            cursor.executescript(schema_sql)
        
        conn.commit()
        print("Database initialized successfully")
        return True
        
    except Exception as e:
        print(f"Database initialization error: {e}")
        return False
    finally:
        conn.close()


# Export for convenience
__all__ = ['get_db_connection', 'execute_query', 'init_database', 'IS_PRODUCTION']
