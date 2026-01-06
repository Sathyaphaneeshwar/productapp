import sqlite3
from typing import Optional
import os

# We need to access the DB. 
# Ideally we'd inject the connection, but for now we'll create a new one or pass it in.
# Importing DB_PATH from config would be better.
import sys
from config import DATABASE_PATH

DEFAULT_PROMPT_TEXT = """
You are an expert financial analyst. 
Analyze the provided earnings call transcript and provide a detailed summary, 
highlighting key financial metrics, strategic initiatives, and potential risks.
"""

class PromptService:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DATABASE_PATH)

    def get_db_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def resolve_prompt(self, stock_id: int) -> str:
        """
        Resolves the system prompt for a given stock.

        Logic:
        1. Find a group this stock belongs to (if multiple, take the first we find).
        2. Return that group's 'stock_summary_prompt' if present.
        3. If no group or no prompt, return the default prompt.
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Use one group's stock_summary_prompt for this stock (any group; use updated_at/added_at ordering)
            query = """
                SELECT g.stock_summary_prompt
                FROM groups g
                JOIN group_stocks gs ON g.id = gs.group_id
                WHERE gs.stock_id = ? AND g.is_active = 1
                ORDER BY gs.updated_at DESC, gs.added_at DESC
                LIMIT 1
            """
            
            cursor.execute(query, (stock_id,))
            result = cursor.fetchone()
            
            if result and result['stock_summary_prompt']:
                return result['stock_summary_prompt']
            
            return self._get_default_prompt()
            
        finally:
            conn.close()

    def _get_default_prompt(self) -> str:
        """Returns the default system prompt if no group-specific prompt is found."""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT setting_value 
                FROM llm_settings 
                WHERE setting_key = 'default_prompt'
                ORDER BY updated_at DESC 
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row and row['setting_value']:
                return row['setting_value']
            return DEFAULT_PROMPT_TEXT
        finally:
            conn.close()
