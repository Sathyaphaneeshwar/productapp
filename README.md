# Stock Database System

A Python-based system to manage NSE and BSE stock data from CSV files with daily update capability.

## Features

- SQLite database for storing stock information
- Import stocks from NSE (EQUITY_L.csv) and BSE (Equity.csv)
- Daily update script that adds only new stocks (based on ISIN)
- Comprehensive logging
- No external dependencies (uses Python standard library)

## Database Schema

The `stocks` table contains:
- `stock_symbol` - NSE stock symbol
- `bse_code` - BSE security code
- `isin_number` - ISIN number (unique identifier)
- `stock_name` - Company/stock name
- `created_at` - Record creation timestamp
- `updated_at` - Last update timestamp

## Setup

1. Ensure you have Python 3.8+ installed
2. Place your CSV files in the `data/` directory:
   - `EQUITY_L.csv` (NSE stocks)
   - `Equity.csv` (BSE stocks)

## Usage

### Initial Database Setup

Run this once to create the database and import initial data:

```bash
cd scripts
python init_database.py
```

This will:
- Create the database at `database/stocks.db`
- Import all stocks from both CSV files
- Log results to `logs/stock_updates.log`

### Daily Updates

Run this daily to add new stocks:

```bash
cd scripts
python update_stocks.py
```

This will:
- Check both CSV files for new stocks
- Add only stocks with ISIN numbers not already in the database
- Update BSE codes for existing stocks if missing
- Log all changes

## Directory Structure

```
product-gemini/
├── data/
│   ├── EQUITY_L.csv      # NSE stocks
│   └── Equity.csv         # BSE stocks
├── database/
│   ├── schema.sql         # Database schema
│   └── stocks.db          # SQLite database (created on init)
├── scripts/
│   ├── config.py          # Configuration
│   ├── init_database.py   # Initial setup script
│   └── update_stocks.py   # Daily update script
├── logs/
│   └── stock_updates.log  # Log file (created on first run)
└── README.md
```

## Logging

All operations are logged to:
- Console (stdout)
- `logs/stock_updates.log`

Log entries include timestamps, operation details, and statistics.

## Automation

To run the update script daily, you can use:

**Linux/Mac (cron):**
```bash
# Add to crontab (runs daily at 6 AM)
0 6 * * * cd /path/to/product-gemini/scripts && python update_stocks.py
```

**Windows (Task Scheduler):**
Create a scheduled task to run `update_stocks.py` daily.

## Backend API

The system includes a Flask-based REST API to serve stock data and manage the watchlist.

### Setup

1. Install dependencies:
   ```bash
   cd backend
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r ../requirements.txt
   ```

2. Run the server:
   ```bash
   python app.py
   ```
   The API will be available at `http://localhost:5000`.

### API Endpoints

- **Search Stocks:** `GET /api/stocks?q=query`
- **Get Watchlist:** `GET /api/watchlist`
- **Add to Watchlist:** `POST /api/watchlist` (Body: `{"symbol": "TATASTEEL"}`)
- **Remove from Watchlist:** `DELETE /api/watchlist/<symbol>`

### Groups API

- **Create Group:** `POST /api/groups`
  ```json
  {
    "name": "TATA Group",
    "deep_research_prompt": "Analyze Tata stocks...",
    "stock_summary_prompt": "Summarize performance..."
  }
  ```
- **List Groups:** `GET /api/groups`
- **Get Group Details:** `GET /api/groups/<id>`
- **Update Group:** `PATCH /api/groups/<id>`
  ```json
  { "is_active": false, "name": "New Name" }
  ```
- **Delete Group:** `DELETE /api/groups/<id>`
- **Add Stock to Group:** `POST /api/groups/<id>/stocks`
  ```json
  { "symbol": "TATASTEEL" }
  ```
- **Remove Stock:** `DELETE /api/groups/<id>/stocks/<symbol>`

### Email List API

- **List Emails:** `GET /api/emails?active=true` (optional filter)
- **Get Email:** `GET /api/emails/<id>`
- **Add Email:** `POST /api/emails`
  ```json
  { "email": "user@example.com", "name": "John Doe", "is_active": true }
  ```
- **Update Email:** `PATCH /api/emails/<id>`
  ```json
  { "name": "Jane Doe", "is_active": false }
  ```
- **Delete Email:** `DELETE /api/emails/<id>`

### SMTP Settings API

- **List SMTP Settings:** `GET /api/smtp-settings?active=true` (optional filter)
- **Get SMTP Setting:** `GET /api/smtp-settings/<id>`
- **Add SMTP Setting:** `POST /api/smtp-settings`
  ```json
  {
    "email": "sender@gmail.com",
    "app_password": "abcd efgh ijkl mnop",
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "is_active": true
  }
  ```
- **Update SMTP Setting:** `PATCH /api/smtp-settings/<id>`
  ```json
  { "app_password": "new password", "is_active": false }
  ```
- **Delete SMTP Setting:** `DELETE /api/smtp-settings/<id>`

> **Security Note:** In production, encrypt the `app_password` field before storing in the database.

### SMTP Email Functionality

- **Test SMTP Connection:** `POST /api/smtp/test`
  - Tests with active SMTP config from database:
    ```json
    {}
    ```
  - Or test with custom credentials:
    ```json
    {
      "email": "test@gmail.com",
      "app_password": "app password",
      "smtp_server": "smtp.gmail.com",
      "smtp_port": 587
    }
    ```
  - Returns: `{ "status": "success/error", "message": "..." }`

- **Send Email:** `POST /api/smtp/send`
  ```json
  {
    "to": "recipient@example.com",
    "subject": "Email Subject",
    "body": "Email body content",
    "is_html": false
  }
  ```
  - Uses active SMTP configuration from database
  - Returns: `{ "status": "success/error", "message": "..." }`





