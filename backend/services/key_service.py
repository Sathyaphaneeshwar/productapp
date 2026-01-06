import sqlite3
import os
import sys
from typing import Optional

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.config import DATABASE_PATH

class KeyService:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DATABASE_PATH)

    def get_db_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_api_key(self, provider_name: str) -> Optional[str]:
        """
        Fetches the active API key for a given provider.
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT api_key FROM api_keys 
                WHERE provider_name = ? AND is_active = 1
                LIMIT 1
            """, (provider_name,))
            
            result = cursor.fetchone()
            return result['api_key'] if result else None
        except Exception as e:
            print(f"Error fetching API key for {provider_name}: {e}")
            return None
        finally:
            conn.close()

    def set_api_key(self, provider_name: str, api_key: str):
        """
        Sets or updates an API key.
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO api_keys (provider_name, api_key, is_active)
                VALUES (?, ?, 1)
                ON CONFLICT(provider_name) DO UPDATE SET
                api_key = excluded.api_key,
                is_active = 1
            """, (provider_name, api_key))
            conn.commit()
        except Exception as e:
            print(f"Error setting API key for {provider_name}: {e}")
            raise e
        finally:
            conn.close()
