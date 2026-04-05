#!/usr/bin/env python3
"""
Process IEOD data - Handles ZIP (incoming/) and CSV (main dir) files
"""
import pandas as pd
from pathlib import Path
import json
from datetime import datetime
import re
import zipfile
import requests

TELEGRAM_TOKEN = "8793580045:AAHj3rtvjrkA112KUqzNkueRPCQb_sx0jkE"
CHAT_ID = "8541952881"

NIFTY50_SYMBOLS = [
    'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'HINDUNILVR', 'SBIN',
    'BHARTIARTL', 'ITC', 'KOTAKBANK', 'LT', 'AXISBANK', 'HCLTECH', 'ASIANPAINT',
    'MARUTI', 'SUNPHARMA', 'TITAN', 'BAJFINANCE', 'WIPRO', 'ULTRACEMCO',
    'ADANIENT', 'ADANIPORTS', 'COALINDIA', 'POWERGRID', 'NTPC', 'ONGC',
    'JSWSTEEL', 'TATASTEEL', 'TECHM', 'GRASIM', 'HINDALCO', 'DIVISLAB',
    'DRREDDY', 'CIPLA', 'BRITANNIA', 'EICHERMOT', 'TATACONSUM', 'BPCL',
    'HEROMOTOCO', 'M&M', 'NESTLEIND', 'BAJAJ-AUTO', 'APOLLOHOSP', 'MARICO',
    'TATAMOTORS', 'SBILIFE', 'HDFCLIFE', 'INDUSINDBK', 'UPL', 'HINDPETRO'
]

def extract_symbol(exchange_code):
    if pd.isna(exchange_code):
        return None
    parts = str(exchange_code).split('.')
    if len(parts) > 0:
        base = parts[0]
        base = re.sub(r'^(\d+)', '', base)
        base = re.sub(r'\d{2,4}$', '', base)
        if base in NIFTY50_SYMBOLS:
            return base
    return None

def process_file(filepath):
    print(f"Processing: {filepath.name}")
    try:
        if filepath.suffix == '.zip':
            with zipfile.ZipFile(filepath, 'r') as z:
                csv_files = [f for f in z.namelist() if f.endswith('.csv')]
                if not csv_files:
                    return None
                with z.open(csv_files[0]) as f:
                    df = pd.read_csv(f)
        else:
            df = pd.read_csv(filepath)
        
        print(f"  Loaded {len(df):,} rows")
        
        df['Symbol'] = df['Ticker'].apply(extract_symbol)
        df = df[df['Symbol'].notna()]
        if len(df) == 0:
            return None
        
        df['DateTime'] = pd.to_datetime(df['Date'] + ' ' + df['Time'], format='%d/%m/%Y %H:%M:%S', dayfirst=True)
        df = df[df['DateTime'].dt.minute % 15 == 0]
        df = df[['Symbol', 'DateTime', 'Open', 'High', 'Low', 'Close', 'Volume']]
        df['Date'] = df['DateTime'].dt.date
        
        print(f"  After filter: {len(df):,} rows")
        return df
    except Exception as e:
        print(f"  Error: {e}")
        return None

def main():
    print("="*60)
    print("IEOD Processing - V2 (ZIP + CSV support)")
    print("="*60)
    
    INTRADAY_FILE = Path("/root/nse_strategy/data/intraday/nifty50_15min.csv")
    LOG_FILE = Path("/root/nse_strategy/data/intraday/accumulation_log.json")
    PROCESSED_LOG = Path("/root/nse_strategy/data/intraday/processed_files.txt")
    
    # Load existing
    existing_df = pd.read_csv(INTRADAY_FILE, parse_dates=['DateTime']) if INTRADAY_FILE.exists() else pd.DataFrame()
    processed = set(PROCESSED_LOG.read_text().split('\n')) if PROCESSED_LOG.exists() else set()
    log = json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else {"total_trading_days": 0, "total_rows": 0, "unique_symbols": 0}
    
    # Find new files
    files = []
    files.extend(list(Path("/root/nse_strategy/incoming").glob("GFDLCM_STOCK_*.zip")))
    files.extend(list(Path("/root/nse_strategy").glob("GFDLCM_STOCK_*.csv")))
    
    new_files = [f for f in files if f.name not in processed]
    print(f"Found {len(new_files)} new files\n")
    
    total_new = 0
    for f in new_files:
        df = process_file(f)
        if df is not None and len(df) > 0:
            if INTRADAY_FILE.exists():
                combined = pd.concat([existing_df, df], ignore_index=True).drop_duplicates(subset=['Symbol', 'DateTime'])
            else:
                combined = df
            combined.to_csv(INTRADAY_FILE, index=False)
            existing_df = combined
            total_new += len(df)
            processed.add(f.name)
            with open(PROCESSED_LOG, 'a') as pl:
                pl.write(f.name + '\n')
    
    # Update log
    log['total_rows'] = len(existing_df)
    log['unique_symbols'] = existing_df['Symbol'].nunique() if len(existing_df) > 0 else 0
    log['total_trading_days'] = existing_df['Date'].nunique() if len(existing_df) > 0 else log.get('total_trading_days', 0)
    log['last_updated'] = datetime.now().isoformat()
    LOG_FILE.write_text(json.dumps(log, indent=2))
    
    print(f"\nTotal new rows: {total_new:,}")
    print(f"Total accumulated: {log['total_rows']:,} rows ({log['total_trading_days']} days)")
    
    # Telegram
    if total_new > 0:
        msg = f"📊 IEOD Daily Update\n\nAdded: {total_new:,} rows\nTotal: {log['total_rows']:,} ({log['total_trading_days']} days)"
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                         json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        except:
            pass

if __name__ == "__main__":
    main()
