import sqlite3
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.config import DATABASE_PATH

def update_database_defaults():
    print(f"Updating database at {DATABASE_PATH}...")
    conn = sqlite3.connect(str(DATABASE_PATH))
    cursor = conn.cursor()
    
    try:
        # 1. Set user_max_tokens to max_output_tokens where it is NULL or 0
        print("Updating user_max_tokens...")
        cursor.execute("""
            UPDATE llm_models 
            SET user_max_tokens = max_output_tokens 
            WHERE (user_max_tokens IS NULL OR user_max_tokens = 0) 
            AND max_output_tokens IS NOT NULL
        """)
        print(f"Updated {cursor.rowcount} rows.")
        
        # 2. Enable thinking mode for models that support it
        print("Enabling thinking mode for supported models...")
        cursor.execute("""
            UPDATE llm_models 
            SET user_thinking_enabled = 1 
            WHERE supports_thinking = 1 
            AND (user_thinking_enabled IS NULL OR user_thinking_enabled = 0)
        """)
        print(f"Updated {cursor.rowcount} rows.")
        
        # 3. Set thinking budget to max_output_tokens for thinking models
        print("Setting thinking budget to max...")
        cursor.execute("""
            UPDATE llm_models 
            SET user_thinking_budget = max_output_tokens 
            WHERE supports_thinking = 1 
            AND (user_thinking_budget IS NULL OR user_thinking_budget = 0)
            AND max_output_tokens IS NOT NULL
        """)
        print(f"Updated {cursor.rowcount} rows.")
        
        conn.commit()
        print("Database update complete!")
        
    except Exception as e:
        print(f"Error updating database: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    update_database_defaults()
