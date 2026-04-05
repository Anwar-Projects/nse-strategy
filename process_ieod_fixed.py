#!/usr/bin/env python3
"""
Process IEOD (Intraday End-of-Day) data from Global Datafeeds - FIXED VERSION
Extracts clean symbols and accumulates 15-minute bars
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
from datetime import datetime
import os
import zipfile
import re

# Configuration
INTRADAY_DIR = Path("/root/nse_strategy/data/intraday")
INTRADAY_FILE = INTRADAY_DIR / "nifty50_15min.csv"
LOG_FILE = INTRADAY_DIR / "accumulation_log.json"
PROCESSED_LOG = INTRADAY_DIR / "processed_files.txt"

INTRADAY_DIR.mkdir(parents=True, exist_ok=True)

# Nifty 50 symbols (clean names)
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
    """Extract clean symbol from exchange code like 'RELIANCE.NS.NSE' -> 'RELIANCE'"""
    if pd.isna(exchange_code):
        return None
    
    # Remove exchange suffixes
    parts = str(exchange_code).split('.')
    if len(parts) > 0:
        base = parts[0]
        # Special handling for symbols with numbers at start (like 0MOFSL26)
        # Remove leading numbers and year codes
        base = re.sub(r'^(\d+)', '', base)  # Remove leading digits
        base = re.sub(r'\d+$', '', base)   # Remove trailing digits (year codes)
        
        # Check if it's in Nifty 50
        if base in NIFTY50_SYMBOLS:
            return base
    
    return None

def load_existing_data():
    """Load existing intraday data"""
    if INTRADAY_FILE.exists():
        return pd.read_csv(INTRADAY_FILE, parse_dates=['DateTime'])
    return pd.DataFrame()


def load_accumulation_log():
    """Load accumulation tracking log"""
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            return json.load(f)
    return {
        "total_trading_days": 0,
        "date_range": {"start": None, "end": None},
        "total_rows": 0,
        "unique_symbols": 0,
        "last_updated": None,
        "target_days": 120,
        "estimated_ready_date": None
    }


def save_accumulation_log(log):
    """Save accumulation tracking log"""
    log["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, 'w') as f:
        json.dump(log, f, indent=2)


def find_ieod_files():
    """Find IEOD zip files"""
    search_paths = [
        Path("/root/nse_strategy/incoming"),
        Path("/root/nse_strategy/data/lake"),
        Path("/root/nse_strategy"),
    ]
    
    ieod_files = []
    for path in search_paths:
        if path.exists():
            ieod_files.extend(list(path.glob("GFDLCM_STOCK_*.zip")))
    
    return sorted(ieod_files)


def process_ieod_zip(zip_path: Path) -> pd.DataFrame:
    """Process IEOD zip file - FIXED"""
    print(f"Processing: {zip_path.name}")
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            csv_files = [f for f in z.namelist() if f.endswith('.csv')]
            if not csv_files:
                print(f"  ❌ No CSV in {zip_path.name}")
                return pd.DataFrame()
            
            with z.open(csv_files[0]) as f:
                df = pd.read_csv(f)
        
        print(f"  Loaded {len(df):,} raw rows")
        
        # Extract clean symbol
        df['Symbol'] = df['Ticker'].apply(extract_symbol)
        df = df[df['Symbol'].notna()]  # Keep only Nifty 50 symbols
        
        if len(df) == 0:
            print(f"  ⚠️ No Nifty 50 symbols found")
            return pd.DataFrame()
        
        print(f"  After symbol filter: {len(df):,} rows")
        
        # Parse datetime (DD/MM/YYYY format)
        df['DateTime'] = pd.to_datetime(df['Date'] + ' ' + df['Time'], 
                                         format='%d/%m/%Y %H:%M:%S', dayfirst=True)
        
        # Filter for 15-minute bars
        df = df[df['DateTime'].dt.minute % 15 == 0]
        
        print(f"  After time filter: {len(df):,} rows")
        
        # Select columns
        df = df[['Symbol', 'DateTime', 'Open', 'High', 'Low', 'Close', 'Volume']]
        df['Date'] = df['DateTime'].dt.date
        
        return df
        
    except Exception as e:
        print(f"  ❌ Error: {str(e)[:80]}")
        return pd.DataFrame()


def main():
    print("="*70)
    print("IEOD INTRADAY DATA PROCESSING (FIXED)")
    print("="*70)
    
    # Load existing
    existing_df = load_existing_data()
    print(f"Existing data: {len(existing_df):,} rows")
    
    log = load_accumulation_log()
    print(f"Accumulated: {log['total_trading_days']} days (target: {log['target_days']})")
    
    # Find files
    ieod_files = find_ieod_files()
    print(f"\nFound {len(ieod_files)} IEOD files")
    
    if not ieod_files:
        print("No files to process")
        return
    
    # Load processed log
    processed = set()
    if PROCESSED_LOG.exists():
        processed = set(PROCESSED_LOG.read_text().split('\n'))
    
    new_files = [f for f in ieod_files if f.name not in processed]
    print(f"New files to process: {len(new_files)}")
    
    if not new_files:
        print("No new files")
        return
    
    # Process files
    total_new_rows = 0
    for zip_file in new_files:
        df = process_ieod_zip(zip_file)
        
        if len(df) > 0:
            # Append to existing
            if INTRADAY_FILE.exists():
                existing_df = pd.read_csv(INTRADAY_FILE, parse_dates=['DateTime'])
                combined = pd.concat([existing_df, df], ignore_index=True)
                # Remove duplicates
                combined = combined.drop_duplicates(subset=['Symbol', 'DateTime'])
            else:
                combined = df
            
            combined.to_csv(INTRADAY_FILE, index=False)
            print(f"  ✅ Saved {len(df):,} rows")
            total_new_rows += len(df)
            
            # Mark processed
            with open(PROCESSED_LOG, 'a') as f:
                f.write(zip_file.name + '\n')
    
    # Update log
    if total_new_rows > 0:
        final_df = load_existing_data()
        log['total_rows'] = len(final_df)
        log['unique_symbols'] = final_df['Symbol'].nunique()
        
        if 'Date' in final_df.columns:
            log['total_trading_days'] = final_df['Date'].nunique()
            dates = pd.to_datetime(final_df['Date'], format='%Y-%m-%d', errors='coerce')
            dates = dates.dropna()
            if len(dates) > 0:
                log['date_range']['start'] = dates.min().strftime('%Y-%m-%d')
                log['date_range']['end'] = dates.max().strftime('%Y-%m-%d')
    
    save_accumulation_log(log)
    
    print("\n" + "="*70)
    print("PROCESSING COMPLETE")
    print("="*70)
    print(f"Files processed: {len(new_files)}")
    print(f"New rows added: {total_new_rows:,}")
    print(f"Total accumulated: {log['total_trading_days']} days")
    print(f"Date range: {log['date_range']['start']} to {log['date_range']['end']}")
    print(f"Unique symbols: {log['unique_symbols']}")
    
    # Save status to Telegram
    import requests
    TELEGRAM_TOKEN = "8793580045:AAHj3rtvjrkA112KUqzNkueRPCQb_sx0jkE"
    CHAT_ID = "8541952881"
    msg = f"📊 IEOD Processing Complete%0A" \
          f"Files: {len(new_files)}%0A" \
          f"New rows: {total_new_rows:,}%0A" \
          f"Total days: {log['total_trading_days']}%0A" \
          f"Symbols: {log['unique_symbols']}"
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
    except:
        pass

if __name__ == "__main__":
    main()
