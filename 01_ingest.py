"""
=============================================================================
NSE INTRADAY STRATEGY — STEP 1: DAILY DATA INGESTION
=============================================================================
Run this every day after receiving your daily zip file.
It extracts, filters for liquid equities, and appends to the parquet data lake.

Usage:
    python 01_ingest.py --zip /path/to/GFDLCM_STOCK_DDMMYYYY.zip
    python 01_ingest.py --zip /path/to/GFDLCM_STOCK_DDMMYYYY.zip --force  # re-ingest

Structure created:
    data/
      lake/
        YYYY-MM-DD.parquet   ← one file per trading day
      registry.json          ← tracks all ingested dates + stats
=============================================================================
"""

import os, sys, json, zipfile, argparse, re, shutil
from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np

# ─── PATHS ───────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
DATA_DIR      = BASE_DIR / "data"
LAKE_DIR      = DATA_DIR / "lake"
REGISTRY_PATH = DATA_DIR / "registry.json"
LAKE_DIR.mkdir(parents=True, exist_ok=True)

# ─── LIQUIDITY FILTERS (same as pipeline) ────────────────────────────────────
MIN_CANDLES   = 100       # relaxed for multi-day (some days may be half-sessions)
MIN_TOTAL_VOL = 50_000
MIN_PRICE     = 5.0
EQUITY_SUFFIX = "NSE"     # NSE Cash Market EQ segment stocks

# ─── REGISTRY ────────────────────────────────────────────────────────────────
def load_registry() -> dict:
    if REGISTRY_PATH.exists():
        with open(REGISTRY_PATH) as f:
            return json.load(f)
    return {"days": {}, "tickers": {}, "last_updated": None}

def save_registry(reg: dict):
    reg["last_updated"] = datetime.now().isoformat()
    with open(REGISTRY_PATH, "w") as f:
        json.dump(reg, f, indent=2)

# ─── INGEST ──────────────────────────────────────────────────────────────────
def ingest(zip_path: str, force: bool = False) -> bool:
    zip_path = Path(zip_path)
    if not zip_path.exists():
        print(f"[ERROR] File not found: {zip_path}")
        return False

    # Parse date from filename e.g. GFDLCM_STOCK_20032026.zip → 2026-03-20
    match = re.search(r"(\d{2})(\d{2})(\d{4})", zip_path.stem)
    if not match:
        print(f"[ERROR] Could not parse date from filename: {zip_path.name}")
        print("        Expected format: GFDLCM_STOCK_DDMMYYYY.zip")
        return False
    day, month, year = match.groups()
    trade_date = f"{year}-{month}-{day}"
    out_parquet = LAKE_DIR / f"{trade_date}.parquet"

    reg = load_registry()

    if trade_date in reg["days"] and not force:
        print(f"[SKIP] {trade_date} already ingested. Use --force to re-ingest.")
        return False

    print(f"\n{'='*60}")
    print(f"  INGESTING: {zip_path.name}")
    print(f"  Trade date: {trade_date}")
    print(f"{'='*60}")

    # Handle both zip and raw CSV input
    tmp_dir = DATA_DIR / "tmp_extract"
    tmp_dir.mkdir(exist_ok=True)
    if zip_path.suffix.lower() == ".csv":
        print(f"  [INFO] Raw CSV detected — skipping zip extraction")
        csv_path = zip_path
        csv_files = [zip_path.name]
    else:
        with zipfile.ZipFile(zip_path, "r") as z:
            csv_files = [f for f in z.namelist() if f.endswith(".csv")]
            if not csv_files:
                print("[ERROR] No CSV found in zip")
                return False
            z.extractall(tmp_dir)
            csv_path = tmp_dir / csv_files[0]

    print(f"  Reading CSV: {csv_files[0]} ...")
    df = pd.read_csv(csv_path)

    # Standardise columns
    df.columns = [c.strip() for c in df.columns]
    required = {"Ticker", "Date", "Time", "Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(set(df.columns)):
        print(f"[ERROR] Missing columns. Found: {df.columns.tolist()}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False

    print(f"  Raw rows: {len(df):,}  |  Unique tickers: {df['Ticker'].nunique():,}")
    
    # ============================================
    # DATA QUALITY FILTERS (Priority 1)
    # ============================================
    rows_before = len(df)
    
    # Filter a: Overnight price change > 20%
    df['prev_close_temp'] = df.groupby('Ticker')['Close'].shift(1)
    df['overnight_change_pct'] = ((df['Open'] - df['prev_close_temp']) / df['prev_close_temp'] * 100).abs()
    mask_overnight = (df['overnight_change_pct'] <= 20) | df['overnight_change_pct'].isna()
    removed_overnight = (~mask_overnight).sum()
    df = df[mask_overnight].copy()
    print(f"  [DATA QUALITY] Removed {removed_overnight:,} rows with overnight change > 20%")
    
    # Filter b: Intraday range > 20%
    df['intraday_range_pct'] = ((df['High'] - df['Low']) / df['Open'] * 100)
    mask_range = df['intraday_range_pct'] <= 20
    removed_range = (~mask_range).sum()
    df = df[mask_range].copy()
    print(f"  [DATA QUALITY] Removed {removed_range:,} rows with intraday range > 20%")
    
    # Filter c: Volume = 0 or null
    mask_volume = df['Volume'].notna() & (df['Volume'] > 0)
    removed_volume = (~mask_volume).sum()
    df = df[mask_volume].copy()
    print(f"  [DATA QUALITY] Removed {removed_volume:,} rows with invalid volume")
    
    # Clean up temp columns
    df = df.drop(columns=['prev_close_temp', 'overnight_change_pct', 'intraday_range_pct'], errors='ignore')
    
    rows_after = len(df)
    print(f"  [DATA QUALITY] Rows after filtering: {rows_after:,} (removed {rows_before - rows_after:,} total, {(rows_before - rows_after)/rows_before*100:.2f}%)")
    
    # Filter equity segment
    df["suffix"] = df["Ticker"].str.extract(r'\.([^.]+)\.NSE$')
    # Handle pure .NSE tickers (RELIANCE.NSE has no middle suffix)
    df["is_pure_nse"] = df["Ticker"].str.match(r'^[A-Z0-9&]+\.NSE$')
    if EQUITY_SUFFIX == "NSE":
        eq = df[df["is_pure_nse"] == True].copy()
    else:
        eq = df[df["suffix"] == EQUITY_SUFFIX].copy()
    print(f"  {EQUITY_SUFFIX} segment rows: {len(eq):,}  |  Tickers: {eq['Ticker'].nunique()}")

    # Liquidity filter
    stats = eq.groupby("Ticker").agg(
        candles=("Close", "count"),
        total_vol=("Volume", "sum"),
        avg_price=("Close", "mean")
    ).reset_index()

    liquid = stats[
        (stats["candles"] >= MIN_CANDLES) &
        (stats["total_vol"] >= MIN_TOTAL_VOL) &
        (stats["avg_price"] >= MIN_PRICE)
    ]["Ticker"].tolist()

    eq = eq[eq["Ticker"].isin(liquid)].copy()
    print(f"  Liquid tickers kept: {len(liquid)}")
    if liquid:
        for t in liquid:
            print(f"    • {t}")

    if eq.empty:
        print("[WARN] No liquid tickers found — parquet not written.")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False

    # Parse datetime
    eq["DateTime"] = pd.to_datetime(
        eq["Date"] + " " + eq["Time"], dayfirst=True
    )
    eq["trade_date"] = trade_date

    # Clean up
    keep_cols = ["trade_date", "Ticker", "DateTime", "Open", "High", "Low", "Close", "Volume"]
    eq = eq[keep_cols].rename(columns={"Ticker": "ticker"})
    eq = eq.sort_values(["ticker", "DateTime"]).reset_index(drop=True)

    # Save parquet
    eq.to_parquet(out_parquet, index=False, engine="pyarrow")
    print(f"\n  ✓ Saved: {out_parquet}  ({len(eq):,} rows)")

    # Update registry
    reg["days"][trade_date] = {
        "file"         : str(out_parquet),
        "rows"         : len(eq),
        "tickers"      : liquid,
        "ticker_count" : len(liquid),
        "ingested_at"  : datetime.now().isoformat(),
        "source_zip"   : zip_path.name,
    }
    # Track ticker history across days
    for t in liquid:
        if t not in reg["tickers"]:
            reg["tickers"][t] = []
        if trade_date not in reg["tickers"][t]:
            reg["tickers"][t].append(trade_date)
    save_registry(reg)

    # Cleanup tmp
    shutil.rmtree(tmp_dir, ignore_errors=True)

    # Summary
    print(f"\n  Registry now has {len(reg['days'])} trading day(s):")
    for d in sorted(reg["days"].keys()):
        info = reg["days"][d]
        print(f"    [{d}] {info['ticker_count']} tickers | {info['rows']:,} rows")
    print(f"\n  Tickers seen across all days ({len(reg['tickers'])}):")
    for t, days in sorted(reg["tickers"].items()):
        print(f"    {t:<30} {len(days)} day(s): {', '.join(sorted(days))}")

    return True


def show_status():
    """Print current lake status without ingesting anything."""
    reg = load_registry()
    if not reg["days"]:
        print("\n  Data lake is empty. Run with --zip to ingest your first file.")
        return
    print(f"\n  {'='*55}")
    print(f"  DATA LAKE STATUS")
    print(f"  {'='*55}")
    print(f"  Trading days : {len(reg['days'])}")
    print(f"  Unique tickers: {len(reg['tickers'])}")
    print(f"  Last updated : {reg.get('last_updated','—')}")
    print(f"\n  Days in lake:")
    for d in sorted(reg["days"].keys()):
        info = reg["days"][d]
        print(f"    [{d}]  {info['ticker_count']:2d} tickers | "
              f"{info['rows']:>6,} rows | src: {info['source_zip']}")
    print(f"\n  Tickers with most coverage:")
    coverage = sorted(reg["tickers"].items(), key=lambda x: len(x[1]), reverse=True)
    for t, days in coverage[:15]:
        bar = "█" * len(days)
        print(f"    {t:<32} {bar} ({len(days)}d)")


# ─── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NSE Daily Data Ingestor")
    parser.add_argument("--zip",    type=str, help="Path to daily zip or CSV file")
    parser.add_argument("--force",  action="store_true", help="Re-ingest even if date exists")
    parser.add_argument("--status", action="store_true", help="Show lake status")
    args = parser.parse_args()

    if args.status or not args.zip:
        show_status()
    else:
        success = ingest(args.zip, force=args.force)
        sys.exit(0 if success else 1)
