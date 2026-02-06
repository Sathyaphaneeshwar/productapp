import sqlite3
from typing import Union
from pathlib import Path

from config import DATABASE_PATH

DBPath = Union[str, Path]


def get_db_connection(db_path: DBPath = DATABASE_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn
