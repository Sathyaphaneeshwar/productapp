# Upgrade Instructions

This guide explains how to upgrade to the latest version while preserving all your data.

## What Gets Updated

| Updated (New Code) | Preserved (Your Data) |
|-------------------|----------------------|
| Backend code | Database (stocks.db) |
| Frontend code | API keys (.env files) |
| Scripts | Encryption key |
| Schema definitions | Watchlists, groups, analyses |

---

## Upgrade Steps

### Step 1: Stop the Application

Stop both frontend and backend servers before proceeding.

### Step 2: Backup Your Data (Important!)

```bash
# Create manual backup of your database
cp database/stocks.db database/stocks_manual_backup.db

# Backup your encryption key
cp .encryption_key .encryption_key.backup

# Backup any .env files you have
cp .env .env.backup  # if exists
```

### Step 3: Extract the Update Package

Extract the provided `update_package_XXXXXX.zip` file.

**Option A: Extract to a temporary folder first (Safer)**
```bash
# Extract to temp folder
unzip update_package_XXXXXX.zip -d update_temp/

# Copy code files (not databases)
rsync -av --exclude='*.db' --exclude='.encryption_key' update_temp/ ./

# Clean up
rm -rf update_temp/
```

**Option B: Extract directly over existing installation**
```bash
# The ZIP already excludes data files, so this is safe
unzip -o update_package_XXXXXX.zip
```

### Step 4: Run Database Migration

This safely adds any new tables or columns without losing your data:

```bash
python scripts/migrate_database.py
```

The script will:
- ✓ Create a backup automatically
- ✓ Add any missing tables
- ✓ Add any missing columns to existing tables
- ✓ Never delete or modify your existing data

### Step 5: Install Any New Dependencies

```bash
# Backend dependencies
cd backend
pip install -r requirements.txt

# Frontend dependencies  
cd ../frontend
npm install
```

### Step 6: Restart the Application

Start your backend and frontend servers as usual.

---

## Troubleshooting

### If something goes wrong

Your data is backed up! Restore from:
- `database/backups/stocks_backup_TIMESTAMP.db` (auto-created by migration)
- `database/stocks_manual_backup.db` (your manual backup)

```bash
# Restore database
cp database/backups/stocks_backup_XXXXXX.db database/stocks.db
```

### Migration script fails

If the migration script fails:
1. Check the error message
2. Your original database is untouched (backup was created first)
3. Contact support with the error message

---

## Version History

- **Current Update**: Includes all features and fixes as of the package date
- Check the package filename for the timestamp: `update_package_YYYYMMDD_HHMMSS.zip`
