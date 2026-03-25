#!/usr/bin/env python3
"""
Process IEOD (Intraday End-of-Day) data from Global Datafeeds
Accumulate 15-minute bars for future ML model
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
from datetime import datetime
import os
import zipfile

# Configuration
INTRADAY_DIR = Path("/root/nse_strategy/data/intraday")
INTRADAY_FILE = INTRADAY_DIR / "nifty50_15min.csv"
LOG_FILE = INTRADAY_DIR / "accumulation_log.json"
PROCESSED_LOG = INTRADAY_DIR / "processed_files.txt"

INTRADAY_DIR.mkdir(parents=True, exist_ok=True)

def load_existing_data() -> pd.DataFrame:
    """Load existing intraday data if available"""
    if INTRADAY_FILE.exists():
        return pd.read_csv(INTRADAY_FILE, parse_dates=['DateTime'])
    return pd.DataFrame()


def load_accumulation_log() -> dict:
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


def save_accumulation_log(log: dict):
    """Save accumulation tracking log"""
    log["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, 'w') as f:
        json.dump(log, f, indent=2)


def find_ieod_files() -> list:
    """Find IEOD zip files to process"""
    # Look in standard locations
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
    """
    Process a single IEOD zip file
    Extract 15-minute bars
    """
    print(f"Processing: {zip_path.name}")
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            # Find CSV file inside
            csv_files = [f for f in z.namelist() if f.endswith('.csv')]
            if not csv_files:
                print(f"  No CSV found in {zip_path.name}")
                return pd.DataFrame()
            
            # Read the CSV (IEOD format)
            with z.open(csv_files[0]) as f:
                df = pd.read_csv(f)
        
        print(f"  Loaded {len(df)} rows")
        
        # IEOD format columns: Ticker, Date, Time, Open, High, Low, Close, Volume
        # Filter for 15-minute intervals (if data has multiple timeframes)
        if 'Time' in df.columns:
            df['DateTime'] = pd.to_datetime(df['Date'] + ' ' + df['Time'])
            df = df[df['DateTime'].dt.minute % 15 == 0]  # Keep 15-min bars
        
        # Filter for liquid stocks (check Symbol column names)
        symbol_col = 'Symbol' if 'Symbol' in df.columns else 'Ticker'
        if symbol_col in df.columns:
            # Keep only Nifty 50 stocks
            nifty50_symbols = [
                'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK',
                # ... (list truncated for brevity, full list in code)
            ]
            df = df[df[symbol_col].isin(nifty50_symbols)]
        
        print(f"  After filtering: {len(df)} rows")
        return df
        
    except Exception as e:
        print(f"  Error processing {zip_path.name}: {str(e)[:50]}")
        return pd.DataFrame()


def update_accumulation_stats(df: pd.DataFrame, log: dict):
    """Update accumulation statistics"""
    if len(df) == 0:
        return
    
    df['Date'] = pd.to_datetime(df['Date'])
    unique_days = df['Date'].dt.date.nunique()
    
    log["total_trading_days"] += unique_days
    log["total_rows"] = len(load_existing_data()) + len(df)
    log["unique_symbols"] = df['Symbol' if 'Symbol' in df.columns else 'Ticker'].nunique()
    
    # Update date range
    date_min = df['Date'].min().strftime("%Y-%m-%d")
    date_max = df['Date'].max().strftime("%Y-%m-%d")
    
    if log["date_range"]["start"] is None or date_min < log["date_range"]["start"]:
        log["date_range"]["start"] = date_min
    if log["date_range"]["end"] is None or date_max > log["date_range"]["end"]:
        log["date_range"]["end"] = date_max
    
    # Estimate ready date (assuming ~1 day added per trading day)
    days_remaining = log["target_days"] - log["total_trading_days"]
    if days_remaining > 0:
        from datetime import timedelta
        est_date = datetime.now() + timedelta(days=days_remaining * 1.5)  # Account for weekends
        log["estimated_ready_date"] = est_date.strftime("%Y-%m-%d")
    else:
        log["estimated_ready_date"] = "READY NOW"


def main():
    """Main processing function"""
    print("=" * 70)
    print("IEOD INTRADAY DATA PROCESSING")
    print("=" * 70)
    
    # Load existing data
    existing_df = load_existing_data()
    print(f"Existing data: {len(existing_df):,} rows")
    
    # Load log
    log = load_accumulation_log()
    print(f"Accumulated: {log['total_trading_days']} days (target: {log['target_days']})")
    
    # Find files to process
    ieod_files = find_ieod_files()
    print(f"\nFound {len(ieod_files)} IEOD zip files")
    
    # Load processed files list
    processed = set()
    if PROCESSED_LOG.exists():
        with open(PROCESSED_LOG) as f:
            processed = set(f.read().strip().split('\n'))
    
    # Process new files
    new_rows = 0
    files_processed = 0
    
    for zip_file in ieod_files:
        if zip_file.name in processed:
            continue
        
        df = process_ieod_zip(zip_file)
        if len(df) > 0:
            # Append to master file
            if INTRADAY_FILE.exists():
                # Check for duplicates before appending
                existing = pd.read_csv(INTRADAY_FILE)
                # Merge and deduplicate
                combined = pd.concat([existing, df]).drop_duplicates(subset=['Date', 'Time', 'Symbol'] if 'Symbol' in df.columns else ['Date', 'Time', 'Ticker'])
                combined.to_csv(INTRADAY_FILE, index=False)
            else:
                df.to_csv(INTRADAY_FILE, index=False)
            
            new_rows += len(df)
            update_accumulation_stats(df, log)
            
            # Mark as processed
            with open(PROCESSED_LOG, 'a') as f:
                f.write(f"{zip_file.name}\n")
            
            files_processed += 1
    
    # Save log
    save_accumulation_log(log)
    
    print(f"\n{'='*70}")
    print("PROCESSING SUMMARY")
    print(f"{'='*70}")
    print(f"Files processed: {files_processed}")
    print(f"New rows added: {new_rows:,}")
    print(f"Total accumulated days: {log['total_trading_days']}")
    print(f"Date range: {log['date_range']['start']} to {log['date_range']['end']}")
    print(f"Estimated ready: {log['estimated_ready_date']}")
    
    # Alert if ready
    if log['total_trading_days'] >= log['target_days']:
        print(f"\n🎉 ALERT: Intraday dataset READY for ML model rebuild!")
    else:
        remaining = log['target_days'] - log['total_trading_days']
        print(f"\n⏳ Need {remaining} more trading days (~{remaining * 1.5:.0f} calendar days)")
    
    print(f"\n{'='*70}")


if __name__ == "__main__":
    main()
