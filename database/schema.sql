-- Stock Database Schema
-- This schema stores stock information from NSE and BSE exchanges

CREATE TABLE IF NOT EXISTS stocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_symbol TEXT,                    -- NSE stock symbol
    bse_code TEXT,                        -- BSE security code
    isin_number TEXT NOT NULL UNIQUE,     -- ISIN number (unique identifier)
    stock_name TEXT NOT NULL,             -- Company/stock name
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for faster lookups
CREATE INDEX IF NOT EXISTS idx_stock_symbol ON stocks(stock_symbol);
CREATE INDEX IF NOT EXISTS idx_bse_code ON stocks(bse_code);
CREATE UNIQUE INDEX IF NOT EXISTS idx_isin_number ON stocks(isin_number);

-- Watchlist Table
CREATE TABLE IF NOT EXISTS watchlist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE,
    UNIQUE(stock_id) -- Prevents adding the same stock twice
);

-- Index for watchlist lookups
CREATE INDEX IF NOT EXISTS idx_watchlist_stock_id ON watchlist_items(stock_id);

-- Groups Table
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    deep_research_prompt TEXT,  -- Custom prompt for this group
    stock_summary_prompt TEXT,  -- Summary prompt for this group
    is_active BOOLEAN DEFAULT 1, -- 1 for active, 0 for inactive
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Group Stocks Link Table (Many-to-Many)
CREATE TABLE IF NOT EXISTS group_stocks (
    group_id INTEGER NOT NULL,
    stock_id INTEGER NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- Track when this link was last modified/re-added
    PRIMARY KEY (group_id, stock_id),
    FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE,
    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
);

-- Email List Table
CREATE TABLE IF NOT EXISTS email_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for email list
CREATE INDEX IF NOT EXISTS idx_email_list_email ON email_list(email);
CREATE INDEX IF NOT EXISTS idx_email_list_active ON email_list(is_active);

-- SMTP Settings Table
CREATE TABLE IF NOT EXISTS smtp_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    app_password TEXT NOT NULL,  -- Store encrypted in production
    smtp_server TEXT DEFAULT 'smtp.gmail.com',
    smtp_port INTEGER DEFAULT 587,
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for SMTP settings
CREATE INDEX IF NOT EXISTS idx_smtp_settings_active ON smtp_settings(is_active);

-- Triggers to update the updated_at timestamp
CREATE TRIGGER IF NOT EXISTS update_stocks_timestamp 
AFTER UPDATE ON stocks
FOR EACH ROW
BEGIN
    UPDATE stocks SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_email_list_timestamp 
AFTER UPDATE ON email_list
FOR EACH ROW
BEGIN
    UPDATE email_list SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_smtp_settings_timestamp 
AFTER UPDATE ON smtp_settings
FOR EACH ROW
BEGIN
    UPDATE smtp_settings SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Transcripts Table (Normalized)
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL,
    quarter TEXT NOT NULL,
    year INTEGER NOT NULL,
    source_url TEXT,                    -- Link to the PDF/source
    content_path TEXT,                  -- Path to local storage of extracted text
    status TEXT DEFAULT 'available', -- 'available', 'upcoming'
    event_date TIMESTAMP, -- For upcoming calls
    analysis_status TEXT, -- 'in_progress', 'done', 'error'
    analysis_error TEXT, -- Stores last analysis error (if any)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE,
    UNIQUE(stock_id, quarter, year)
);

-- Transcript Check Status Table (for polling visibility)
CREATE TABLE IF NOT EXISTS transcript_checks (
    stock_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'idle', -- idle, checking
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_transcript_checks_status ON transcript_checks(status);

-- Transcript Analyses Table
CREATE TABLE IF NOT EXISTS transcript_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id INTEGER NOT NULL,
    prompt_snapshot TEXT,               -- The exact prompt used for this analysis
    llm_output TEXT,                    -- The AI generated summary/analysis
    model_provider TEXT,                -- e.g., 'gemini', 'openai' (deprecated, use model_id)
    model_id INTEGER,                   -- FK to llm_models
    thinking_mode_used BOOLEAN DEFAULT 0,
    tokens_used_input INTEGER,
    tokens_used_output INTEGER,
    cost_usd REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (transcript_id) REFERENCES transcripts(id) ON DELETE CASCADE,
    FOREIGN KEY (model_id) REFERENCES llm_models(id)
);

-- Index for transcript lookups
CREATE INDEX IF NOT EXISTS idx_transcripts_stock ON transcripts(stock_id);
CREATE INDEX IF NOT EXISTS idx_analyses_transcript ON transcript_analyses(transcript_id);

-- Transcript Fetch Schedule (Queue-First Scheduler)
CREATE TABLE IF NOT EXISTS transcript_fetch_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL,
    quarter TEXT NOT NULL,
    year INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    next_check_at TIMESTAMP,
    last_status TEXT,
    last_checked_at TIMESTAMP,
    last_available_at TIMESTAMP,
    attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_id, quarter, year),
    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fetch_schedule_next ON transcript_fetch_schedule(next_check_at);
CREATE INDEX IF NOT EXISTS idx_fetch_schedule_priority ON transcript_fetch_schedule(priority);

-- Transcript Events (Normalized)
CREATE TABLE IF NOT EXISTS transcript_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL,
    quarter TEXT NOT NULL,
    year INTEGER NOT NULL,
    status TEXT NOT NULL,
    source_url TEXT,
    event_date TIMESTAMP,
    observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    origin TEXT,
    FOREIGN KEY (stock_id) REFERENCES stocks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_transcript_events_stock ON transcript_events(stock_id);
CREATE INDEX IF NOT EXISTS idx_transcript_events_quarter ON transcript_events(quarter, year);

-- Analysis Jobs (Queue)
CREATE TABLE IF NOT EXISTS analysis_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL,
    force INTEGER NOT NULL DEFAULT 0,
    retry_next_at TIMESTAMP,
    locked_until TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(idempotency_key),
    FOREIGN KEY (transcript_id) REFERENCES transcripts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_analysis_jobs_status ON analysis_jobs(status);
CREATE INDEX IF NOT EXISTS idx_analysis_jobs_retry ON analysis_jobs(retry_next_at);

-- Durable Queue Messages (SQLite-backed broker)
CREATE TABLE IF NOT EXISTS queue_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_name TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_queue_messages_due ON queue_messages(queue_name, available_at, id);

-- Email Outbox (Queue)
CREATE TABLE IF NOT EXISTS email_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL,
    recipient TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    scheduled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    retry_next_at TIMESTAMP,
    locked_until TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(analysis_id, recipient),
    FOREIGN KEY (analysis_id) REFERENCES transcript_analyses(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_email_outbox_status ON email_outbox(status);
CREATE INDEX IF NOT EXISTS idx_email_outbox_retry ON email_outbox(retry_next_at);

-- Group Deep Research Runs (per group, per quarter)
CREATE TABLE IF NOT EXISTS group_research_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    quarter TEXT NOT NULL,
    year INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending, in_progress, done, error
    prompt_snapshot TEXT,
    llm_output TEXT,
    model_provider TEXT,
    model_id TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(group_id, quarter, year),
    FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_group_runs_group ON group_research_runs(group_id);
CREATE INDEX IF NOT EXISTS idx_group_runs_status ON group_research_runs(status);

-- API Keys Table
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_name TEXT NOT NULL UNIQUE, -- e.g., 'tijori', 'gemini'
    api_key TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- LLM Providers Table
CREATE TABLE IF NOT EXISTS llm_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_name TEXT UNIQUE NOT NULL, -- 'google_ai', 'openai', 'anthropic', 'openrouter'
    display_name TEXT NOT NULL, -- 'Google AI Studio', 'OpenAI', etc.
    api_key_encrypted TEXT, -- Encrypted API key
    is_active BOOLEAN DEFAULT 1,
    base_url TEXT, -- For OpenRouter or custom endpoints
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- LLM Models Table
CREATE TABLE IF NOT EXISTS llm_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL,
    model_id TEXT NOT NULL, -- e.g., 'gpt-4o', 'claude-3-5-sonnet'
    display_name TEXT NOT NULL, -- 'GPT-4o', 'Claude 3.5 Sonnet'
    supports_thinking BOOLEAN DEFAULT 0, -- True for o1, future Claude models
    context_window INTEGER, -- Max tokens
    cost_per_1m_input REAL, -- Cost per 1M input tokens (USD)
    cost_per_1m_output REAL, -- Cost per 1M output tokens (USD)
    is_active BOOLEAN DEFAULT 1,
    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (provider_id) REFERENCES llm_providers(id) ON DELETE CASCADE,
    UNIQUE(provider_id, model_id)
);

-- LLM Settings Table
CREATE TABLE IF NOT EXISTS llm_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    setting_key TEXT UNIQUE NOT NULL, -- 'default_model_id', 'default_provider_id', 'spending_limit_usd'
    setting_value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for LLM tables
CREATE INDEX IF NOT EXISTS idx_llm_models_provider ON llm_models(provider_id);
CREATE INDEX IF NOT EXISTS idx_llm_models_active ON llm_models(is_active);

-- Add updated_at to group_stocks (Note: SQLite doesn't support ALTER TABLE ADD COLUMN with constraints easily, 
-- so we'll just add it if it doesn't exist, or rely on a migration script for existing data. 
-- For this schema file which defines the *desired* state, we update the definition.)
-- RE-DEFINING group_stocks to include updated_at
-- In a real migration we would alter, but here we update the schema definition for new installs.
-- Existing users might need a migration step.
