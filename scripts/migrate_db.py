import sqlite3
import os
import sys

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.config import DATABASE_PATH, SCHEMA_PATH

def migrate():
    print(f"Migrating database at {DATABASE_PATH}")
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # 1. Apply new tables from schema.sql
    with open(SCHEMA_PATH, 'r') as f:
        schema_sql = f.read()
        cursor.executescript(schema_sql)
    
    # 2. Manually check and add columns that might be missing (SQLite doesn't do this in CREATE IF NOT EXISTS)
    # Check group_stocks for updated_at
    cursor.execute("PRAGMA table_info(group_stocks)")
    columns = [info[1] for info in cursor.fetchall()]
    
    if 'updated_at' not in columns:
        print("Adding updated_at to group_stocks...")
        try:
            # SQLite limitation: Cannot add column with dynamic default (CURRENT_TIMESTAMP)
            cursor.execute("ALTER TABLE group_stocks ADD COLUMN updated_at TIMESTAMP")
            cursor.execute("UPDATE group_stocks SET updated_at = CURRENT_TIMESTAMP")
        except Exception as e:
            print(f"Error adding column: {e}")

    # 3. Check for api_keys table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='api_keys'")
    if not cursor.fetchone():
        print("Creating api_keys table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_name TEXT NOT NULL UNIQUE,
                api_key TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
    # Seed Tijori Key (Always try to insert/ignore)
    print("Seeding Tijori API Key...")
    cursor.execute("""
        INSERT OR IGNORE INTO api_keys (provider_name, api_key)
        VALUES ('tijori', '5e8e522a21cc43f88939875cc7dc5673')
    """)

    # 4. Add status and event_date to transcripts table
    cursor.execute("PRAGMA table_info(transcripts)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'status' not in columns:
        print("Adding status column to transcripts...")
        try:
            cursor.execute("ALTER TABLE transcripts ADD COLUMN status TEXT DEFAULT 'available'")
        except Exception as e:
            print(f"Error adding status column: {e}")
    
    if 'event_date' not in columns:
        print("Adding event_date column to transcripts...")
        try:
            cursor.execute("ALTER TABLE transcripts ADD COLUMN event_date TIMESTAMP")
        except Exception as e:
            print(f"Error adding event_date column: {e}")

    if 'analysis_status' not in columns:
        print("Adding analysis_status column to transcripts...")
        try:
            cursor.execute("ALTER TABLE transcripts ADD COLUMN analysis_status TEXT")
        except Exception as e:
            print(f"Error adding analysis_status column: {e}")

    if 'analysis_error' not in columns:
        print("Adding analysis_error column to transcripts...")
        try:
            cursor.execute("ALTER TABLE transcripts ADD COLUMN analysis_error TEXT")
        except Exception as e:
            print(f"Error adding analysis_error column: {e}")

    if 'updated_at' not in columns:
        print("Adding updated_at column to transcripts...")
        try:
            cursor.execute("ALTER TABLE transcripts ADD COLUMN updated_at TIMESTAMP")
            cursor.execute("UPDATE transcripts SET updated_at = CURRENT_TIMESTAMP")
        except Exception as e:
            print(f"Error adding updated_at column: {e}")

    # 4b. Add transcript_checks table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transcript_checks'")
    if not cursor.fetchone():
        print("Creating transcript_checks table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transcript_checks (
                stock_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'idle',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_transcript_checks_status ON transcript_checks(status)
        """)

    # 5. Add new columns to transcript_analyses table
    cursor.execute("PRAGMA table_info(transcript_analyses)")
    columns = [col[1] for col in cursor.fetchall()]
    
    new_columns = {
        'model_id': 'INTEGER',
        'thinking_mode_used': 'BOOLEAN DEFAULT 0',
        'tokens_used_input': 'INTEGER',
        'tokens_used_output': 'INTEGER',
        'cost_usd': 'REAL'
    }
    
    for col_name, col_type in new_columns.items():
        if col_name not in columns:
            print(f"Adding {col_name} column to transcript_analyses...")
            try:
                cursor.execute(f"ALTER TABLE transcript_analyses ADD COLUMN {col_name} {col_type}")
            except Exception as e:
                print(f"Error adding {col_name} column: {e}")

    # 6. Seed LLM Providers
    print("Seeding LLM providers...")
    providers = [
        ('google_ai', 'Google AI Studio', None),
        ('openai', 'OpenAI', None),
        ('anthropic', 'Anthropic', None),
        ('openrouter', 'OpenRouter', 'https://openrouter.ai/api/v1')
    ]
    
    for provider_name, display_name, base_url in providers:
        cursor.execute("""
            INSERT OR IGNORE INTO llm_providers (provider_name, display_name, base_url)
            VALUES (?, ?, ?)
        """, (provider_name, display_name, base_url))
    
    # 7. Seed initial models (will be updated by auto-sync later)
    print("Seeding initial LLM models...")
    cursor.execute("SELECT id FROM llm_providers WHERE provider_name = 'google_ai'")
    google_provider_id = cursor.fetchone()
    
    if google_provider_id:
        google_provider_id = google_provider_id[0]
        initial_models = [
            (google_provider_id, 'gemini-1.5-pro', 'Gemini 1.5 Pro', 0, 2000000, 1.25, 5.0),
            (google_provider_id, 'gemini-1.5-flash', 'Gemini 1.5 Flash', 0, 1000000, 0.075, 0.30),
        ]
        
        for provider_id, model_id, display_name, supports_thinking, context_window, cost_input, cost_output in initial_models:
            cursor.execute("""
                INSERT OR IGNORE INTO llm_models 
                (provider_id, model_id, display_name, supports_thinking, context_window, cost_per_1m_input, cost_per_1m_output)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (provider_id, model_id, display_name, supports_thinking, context_window, cost_input, cost_output))
    
    # Seed OpenAI models (known models as of 2024/2025)
    cursor.execute("SELECT id FROM llm_providers WHERE provider_name = 'openai'")
    openai_provider_id = cursor.fetchone()
    if openai_provider_id:
        openai_provider_id = openai_provider_id[0]
        openai_models = [
            (openai_provider_id, 'gpt-4o', 'GPT-4o', 0, 128000, 5.0, 15.0),
            (openai_provider_id, 'gpt-4-turbo', 'GPT-4 Turbo', 0, 128000, 10.0, 30.0),
            (openai_provider_id, 'gpt-3.5-turbo', 'GPT-3.5 Turbo', 0, 16385, 0.5, 1.5),
            (openai_provider_id, 'o1-preview', 'o1 Preview (Thinking)', 1, 128000, 15.0, 60.0),
            (openai_provider_id, 'o1-mini', 'o1 Mini (Thinking)', 1, 128000, 3.0, 12.0),
        ]
        
        for provider_id, model_id, display_name, supports_thinking, context_window, cost_input, cost_output in openai_models:
            cursor.execute("""
                INSERT OR IGNORE INTO llm_models 
                (provider_id, model_id, display_name, supports_thinking, context_window, cost_per_1m_input, cost_per_1m_output)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (provider_id, model_id, display_name, supports_thinking, context_window, cost_input, cost_output))
    
    # Seed Anthropic models
    cursor.execute("SELECT id FROM llm_providers WHERE provider_name = 'anthropic'")
    anthropic_provider_id = cursor.fetchone()
    if anthropic_provider_id:
        anthropic_provider_id = anthropic_provider_id[0]
        anthropic_models = [
            (anthropic_provider_id, 'claude-3-opus-20240229', 'Claude 3 Opus', 0, 200000, 15.0, 75.0),
            (anthropic_provider_id, 'claude-3-5-sonnet-20241022', 'Claude 3.5 Sonnet', 0, 200000, 3.0, 15.0),
            (anthropic_provider_id, 'claude-3-5-haiku-20241022', 'Claude 3.5 Haiku', 0, 200000, 0.25, 1.25),
        ]
        
        for provider_id, model_id, display_name, supports_thinking, context_window, cost_input, cost_output in anthropic_models:
            cursor.execute("""
                INSERT OR IGNORE INTO llm_models 
                (provider_id, model_id, display_name, supports_thinking, context_window, cost_per_1m_input, cost_per_1m_output)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (provider_id, model_id, display_name, supports_thinking, context_window, cost_input, cost_output))
    
    # 8. Add user config columns to llm_models
    cursor.execute("PRAGMA table_info(llm_models)")
    columns = [col[1] for col in cursor.fetchall()]
    
    new_model_columns = {
        'user_max_tokens': 'INTEGER',
        'user_thinking_enabled': 'BOOLEAN DEFAULT 0',
        'user_thinking_budget': 'INTEGER',
        'max_output_tokens': 'INTEGER'
    }
    
    for col_name, col_type in new_model_columns.items():
        if col_name not in columns:
            print(f"Adding {col_name} column to llm_models...")
            try:
                cursor.execute(f"ALTER TABLE llm_models ADD COLUMN {col_name} {col_type}")
            except Exception as e:
                print(f"Error adding {col_name} column: {e}")

    # 9. Seed max_output_tokens for known models
    print("Updating max_output_tokens for known models...")
    model_limits = {
        'gemini-1.5-pro': 8192,
        'gemini-1.5-flash': 8192,
        'gpt-4o': 4096,
        'gpt-4-turbo': 4096,
        'gpt-3.5-turbo': 4096,
        'o1-preview': 32768,
        'o1-mini': 65536,
        'claude-3-opus-20240229': 4096,
        'claude-3-5-sonnet-20241022': 8192,
        'claude-3-5-haiku-20241022': 4096,
    }
    
    for model_id, limit in model_limits.items():
        cursor.execute("UPDATE llm_models SET max_output_tokens = ? WHERE model_id = ?", (limit, model_id))

    # 10. Set default LLM settings (Gemini 1.5 Pro as default)
    print("Setting default LLM settings...")
    cursor.execute("SELECT id FROM llm_models WHERE model_id = 'gemini-1.5-pro'")
    default_model = cursor.fetchone()
    
    if default_model:
        default_model_id = default_model[0]
        cursor.execute("""
            INSERT OR REPLACE INTO llm_settings (setting_key, setting_value)
            VALUES ('default_model_id', ?)
        """, (str(default_model_id),))
        
        cursor.execute("""
            INSERT OR REPLACE INTO llm_settings (setting_key, setting_value)
            VALUES ('default_provider_id', ?)
        """, (str(google_provider_id),))
    
    # Set default spending limit (100 USD)
    cursor.execute("""
        INSERT OR REPLACE INTO llm_settings (setting_key, setting_value)
        VALUES ('spending_limit_usd', '100.0')
    """)

    conn.commit()
    
    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
