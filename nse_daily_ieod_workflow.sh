#!/bin/bash
# NSE Daily IEOD Workflow - Complete automation
# 1. Fetch new emails from Gmail
# 2. Download attachments
# 3. Process IEOD data
# 4. Send Telegram notification

echo "=========================================="
echo "NSE IEOD Daily Workflow - $(date)"
echo "=========================================="

cd /root/nse_strategy

# Step 1: Fetch new emails (if Gmail credentials configured)
if [ -n "$GMAIL_APP_PASSWORD" ]; then
    echo "[1/3] Fetching new emails from Gmail..."
    python3 fetch_gmail_ieod.py 2>&1
else
    echo "[1/3] SKIP: No GMAIL_APP_PASSWORD set"
    echo "      To enable auto-fetch: export GMAIL_APP_PASSWORD=your_app_password"
fi

# Step 2: Process IEOD data (both ZIP and CSV)
echo "[2/3] Processing IEOD data..."
python3 paper_trading/process_ieod_intraday.py >> logs/intraday_$(date +%Y%m%d).log 2>&1

# Step 3: Check status
echo "[3/3] Checking accumulation status..."
if [ -f data/intraday/accumulation_log.json ]; then
    cat data/intraday/accumulation_log.json | python3 -c "
import json,sys
log=json.load(sys.stdin)
print(f\"Total rows: {log.get('total_rows', 0):,}\")
print(f\"Trading days: {log.get('total_trading_days', 0)}\")
print(f\"Unique symbols: {log.get('unique_symbols', 0)}\")
"
fi

echo "=========================================="
echo "Workflow complete: $(date)"
echo "=========================================="
