#!/bin/bash

# Create Update Package Script
# This script creates a code-only ZIP file for distribution
# It excludes all data files (databases, logs, keys, etc.)

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$PROJECT_ROOT"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
OUTPUT_FILE="$OUTPUT_DIR/update_package_$TIMESTAMP.zip"

echo "================================================"
echo "  Creating Update Package"
echo "================================================"
echo ""
echo "Project root: $PROJECT_ROOT"
echo "Output file:  $OUTPUT_FILE"
echo ""

cd "$PROJECT_ROOT"

# Create the ZIP with exclusions
# Note: zip patterns need proper glob syntax
zip -r "$OUTPUT_FILE" . \
    -x "*.db" \
    -x "*.db-journal" \
    -x "*.log" \
    -x ".encryption_key" \
    -x ".env" \
    -x ".env.*" \
    -x "*/venv/*" \
    -x "venv/*" \
    -x "*_venv/*" \
    -x "*/*_venv/*" \
    -x "backend/backend_venv/*" \
    -x ".venv/*" \
    -x "*/.venv/*" \
    -x "*/.pack-venv/*" \
    -x ".pack-venv/*" \
    -x "backend/.pack-venv/*" \
    -x "*env/*" \
    -x "*/node_modules/*" \
    -x "node_modules/*" \
    -x "frontend/node_modules/*" \
    -x "*/__pycache__/*" \
    -x "__pycache__/*" \
    -x "*.pyc" \
    -x ".git/*" \
    -x "*/.git/*" \
    -x "*.zip" \
    -x "*/logs/*" \
    -x "logs/*" \
    -x "backend/dist/*" \
    -x "*/dist/*" \
    -x "frontend/dist/*" \
    -x "frontend/build/*" \
    -x "frontend/release/*" \
    -x "*/release/*" \
    -x ".DS_Store" \
    -x "*/.DS_Store" \
    -x "*.egg-info/*" \
    -x "update_package_*.zip"

echo ""
echo "================================================"
echo "  Package created successfully!"
echo "================================================"
echo ""
echo "Output: $OUTPUT_FILE"
echo ""
echo "This package EXCLUDES:"
echo "  - All .db database files (their data is preserved)"
echo "  - .encryption_key (their encryption key)"
echo "  - .env files (their API keys)"
echo "  - venv/, node_modules/ (they install fresh)"
echo "  - Log files, cache files"
echo ""
echo "Send this ZIP to the company along with UPGRADE.md"
echo ""
