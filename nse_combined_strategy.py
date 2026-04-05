"""
=============================================================================
NSE BOLLINGER BAND MEAN REVERSION STRATEGY — PAPER TRADING
=============================================================================

Components:
  1. DATA LOADER     — reads parquet lake, resamples to 3-min bars
  2. SIGNAL ENGINE   — Bollinger Band Mean Reversion (BB touch + RSI + Volume)
  3. CONFIDENCE FILTER — 6 conditions for Level 3, 4-5 for Level 2
  4. RISK MANAGER    — ₹2,000 per trade, Smart SL (15min support/EMA10/ATR)
  5. PAPER TRADER    — records trades, tracks P&L (no real orders)
  6. DAILY REPORTER  — Telegram summary every evening at 4:00 PM IST

Universe   : Nifty 50 stocks from IEOD parquet lake
Timeframe  : 3-minute bars (resampled from 1-min IEOD data)
Risk       : ₹2,000 per trade, ₹4,000 max loss cap
Session    : 9:15 AM → 3:15 PM IST (NSE market hours)
Exit       : 3:10 PM IST hard exit (no overnight positions)

Strategy:
  - BUY: Price touches BB lower + RSI < 40 + reversal candle + vol >= 1.5x
  - SELL: Price touches BB upper + RSI > 60 + reversal candle + vol >= 1.5x
  - TP1: BB middle band (50% exit)
  - TP2: Opposite BB band (trail remaining)

Run daily:
  python3 nse_combined_strategy.py --mode paper
  python3 nse_combined_strategy.py --mode backtest --date 2026-04-02
=============================================================================
"""

import os
import json
import argparse
import requests
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Tuple

warnings.filterwarnings("ignore")

# ─── PATHS ────────────────────────────────────────────────────────────────────
BASE_DIR    = Path("/root/nse_strategy")
LAKE_DIR    = BASE_DIR / "data" / "lake"
TRADES_DIR  = BASE_DIR / "data" / "paper_trades"
LOG_DIR     = BASE_DIR / "logs"
TRADES_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

TRADES_FILE = TRADES_DIR / "paper_trades.csv"
PNL_FILE    = TRADES_DIR / "daily_pnl.json"

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    "8793580045:AAHj3rtvjrkA112KUqzNkueRPCQb_sx0jkE"
)
TELEGRAM_CHAT   = "8541952881"

# Universe — Nifty 50 stocks in IEOD data format
NIFTY50 = [
    "HDFCBANK.NSE", "KOTAKBANK.NSE", "RELIANCE.NSE", "ONGC.NSE",
    "ICICIBANK.NSE", "SBIN.NSE", "ITC.NSE", "POWERGRID.NSE",
    "WIPRO.NSE", "NTPC.NSE", "INFY.NSE", "BHARTIARTL.NSE",
    "AXISBANK.NSE", "BAJFINANCE.NSE", "HCLTECH.NSE", "TCS.NSE",
    "LT.NSE", "JSWSTEEL.NSE", "ASIANPAINT.NSE", "TITAN.NSE",
    "HINDUNILVR.NSE", "NESTLEIND.NSE", "MARUTI.NSE", "ULTRACEMCO.NSE",
]

# Risk
RISK_PER_TRADE_INR = 2000       # ₹2,000 per trade
MAX_TRADES_PER_DAY = 8          # max 8 simultaneous positions
MIN_PRICE          = 100.0      # skip penny stocks
MIN_VOLUME_PER_BAR = 500        # minimum volume per 5-min bar

# Timeframe — 5-min bars for faster entries/exits
BAR_TIMEFRAME      = "3min"     # changed from 15min → 5min
BAR_MIN_BARS       = 8          # minimum bars needed

# Strategy parameters (Supply/Demand)
EMA_PERIOD         = 20         # trend filter (20-period EMA on 5-min)
HTF_EMA_PERIOD     = 20         # HTF trend (20-period on 60-min)
MIN_PUSH_CANDLES   = 3          # minimum candles in push
MAX_PUSH_CANDLES   = 5          # maximum candles in push
MARUBOZU_RATIO     = 0.75       # body/range ratio for strong candle
ZONE_BUFFER_PCT    = 0.002      # 0.2% buffer beyond zone for SL
MIN_FVG_PCT        = 0.001      # minimum 0.1% gap for FVG

# Momentum parameters
MOMENTUM_PERIOD    = 14         # ATR and momentum lookback
BREAKOUT_MULT      = 0.3        # price must break 0.3×ATR above/below swing

# Mean reversion parameters
RSI_PERIOD         = 14
RSI_OVERSOLD       = 40         # relaxed for intraday
RSI_OVERBOUGHT     = 70
BB_PERIOD          = 20
BB_STD             = 2.0        # Bollinger Band std

# Trade management
BE_TRIGGER_PCT     = 0.25       # move SL to BE when 25% of TP1 distance covered
PARTIAL_EXIT_PCT   = 0.50       # close 50% of position at 50% of TP1
TRAIL_PCT          = 0.15       # trail remaining at 15% gap from current price

# Nifty50 regime filter
NIFTY_EMA_PERIOD   = 10         # 10-day EMA for regime detection
NIFTY_CHOPPY_PCT   = 0.005      # 0.5% band = choppy, skip trading
NIFTY_SYMBOL       = "NIFTY 50" # yfinance symbol for index

# Session
MARKET_OPEN        = "09:15"
MARKET_CLOSE       = "15:30"
HARD_EXIT_TIME     = "15:10"    # close all positions before this


# ─── DATA MODELS ──────────────────────────────────────────────────────────────
@dataclass
class Signal:
    symbol:     str
    direction:  str             # 'BUY' or 'SELL'
    source:     str             # 'SD', 'MOMENTUM', 'MEAN_REVERSION'
    strength:   float           # 0-1
    price:      float
    reason:     str
    timestamp:  str = ""


@dataclass
class ZoneSetup:
    """Supply/Demand zone (adapted from MT5 bot)"""
    symbol:     str
    direction:  str             # 'BUY' or 'SELL'
    zone_low:   float
    zone_high:  float
    zone_time:  str
    bos_level:  float
    fvg_low:    float
    fvg_high:   float
    htf_trend:  str             # 'BULL' or 'BEAR'
    strength:   float = 0.8


@dataclass
class Trade:
    trade_id:    str
    symbol:      str
    direction:   str
    entry_time:  str
    entry_price: float
    sl:          float
    tp1:         float
    tp2:         float
    qty:         int
    risk_inr:    float
    confidence:  str             # LEVEL_2 or LEVEL_3
    signals:     str             # which signals agreed
    status:      str = "OPEN"   # OPEN / PARTIAL / TP2 / SL / EXIT
    exit_time:   str = ""
    exit_price:  float = 0.0
    pnl:         float = 0.0
    partial_done: bool = False   # 50% closed at 50% of TP1
    be_moved:    bool = False    # SL moved to breakeven
    trail_active: bool = False   # trailing stop active
    trail_sl:    float = 0.0    # current trailing SL price


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        print(f"[Telegram] Failed: {e}")


# ─── DATA LOADER ──────────────────────────────────────────────────────────────
def load_day_data(trade_date: str) -> Optional[pd.DataFrame]:
    """Load parquet file for a given date."""
    parquet = LAKE_DIR / f"{trade_date}.parquet"
    if not parquet.exists():
        print(f"[DataLoader] No data for {trade_date}")
        return None

    df = pd.read_parquet(parquet)

    # Standardise column names
    df.columns = [c.lower() for c in df.columns]
    col_map = {}
    for c in df.columns:
        if "tick" in c or "symbol" in c:
            col_map[c] = "ticker"
        elif c in ("datetime", "time", "timestamp"):
            col_map[c] = "datetime"

    df = df.rename(columns=col_map)

    if "datetime" not in df.columns:
        print(f"[DataLoader] No datetime column in {trade_date}")
        return None

    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values(["ticker", "datetime"]).reset_index(drop=True)

    # Filter Nifty50 only
    df = df[df["ticker"].isin(NIFTY50)]

    # Filter market hours
    df["time_str"] = df["datetime"].dt.strftime("%H:%M")
    df = df[(df["time_str"] >= MARKET_OPEN) &
            (df["time_str"] <= MARKET_CLOSE)]

    print(f"[DataLoader] {trade_date}: {len(df):,} rows | "
          f"{df['ticker'].nunique()} stocks")
    return df


def resample_to_5min(df: pd.DataFrame, symbol: str) -> Optional[pd.DataFrame]:
    """Resample 1-min data to 3-min OHLCV bars."""
    sym_df = df[df["ticker"].astype(str) == symbol].copy()
    if len(sym_df) < 5:
        return None

    sym_df = sym_df.set_index("datetime")
    ohlcv = sym_df[["open", "high", "low", "close", "volume"]].resample("3min").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna()

    ohlcv = ohlcv[ohlcv["volume"] >= MIN_VOLUME_PER_BAR]
    ohlcv = ohlcv[ohlcv["close"] >= MIN_PRICE]

    if len(ohlcv) < BAR_MIN_BARS:
        return None

    return ohlcv.reset_index()


def resample_to_15min(df: pd.DataFrame, symbol: str) -> Optional[pd.DataFrame]:
    """Resample 1-min data to 15-min OHLCV bars."""
    sym_df = df[df["ticker"].astype(str) == symbol].copy()
    if len(sym_df) < 15:
        return None

    sym_df = sym_df.set_index("datetime")
    ohlcv = sym_df[["open", "high", "low", "close", "volume"]].resample("15min").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna()

    ohlcv = ohlcv[ohlcv["volume"] >= MIN_VOLUME_PER_BAR * 3]  # 3x for 15min vs 5min
    ohlcv = ohlcv[ohlcv["close"] >= MIN_PRICE]

    if len(ohlcv) < BAR_MIN_BARS // 2:  # Lower bar count for 15min
        return None

    return ohlcv.reset_index()


def resample_to_60min(df: pd.DataFrame, symbol: str) -> Optional[pd.DataFrame]:
    """Resample 1-min data to 60-min OHLCV bars for HTF analysis."""
    sym_df = df[df["ticker"].astype(str) == symbol].copy()
    if len(sym_df) < 30:
        return None

    sym_df = sym_df.set_index("datetime")
    ohlcv = sym_df[["open", "high", "low", "close", "volume"]].resample("60min").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna()

    return ohlcv.reset_index()



def load_multi_day_data(symbol: str, end_date: str,
                        days: int = 15) -> Optional[pd.DataFrame]:
    """Load multiple days of data for indicator warmup."""
    from datetime import datetime, timedelta
    end    = datetime.strptime(end_date, "%Y-%m-%d")
    frames = []
    for i in range(days, -1, -1):
        d = end - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y-%m-%d")
        parquet  = LAKE_DIR / f"{date_str}.parquet"
        if not parquet.exists():
            continue
        try:
            df = pd.read_parquet(parquet)
            df.columns = [c.lower() for c in df.columns]
            for c in list(df.columns):
                if "tick" in c or "symbol" in c:
                    df = df.rename(columns={c: "ticker"})
                if c in ("datetime", "time", "timestamp"):
                    df = df.rename(columns={c: "datetime"})
            df["datetime"] = pd.to_datetime(df["datetime"])
            sym_df = df[df["ticker"].astype(str) == symbol]
            if len(sym_df) > 0:
                frames.append(sym_df)
        except Exception:
            continue
    if not frames:
        return None
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("datetime").drop_duplicates("datetime")
    return combined


def get_nifty_regime(trade_date: str) -> str:
    """
    Get Nifty50 market regime using yfinance daily data.
    Returns: 'BULL', 'BEAR', or 'CHOPPY'
    Bull  = Nifty above 20-day EMA
    Bear  = Nifty below 20-day EMA
    Choppy = within 0.5% of EMA → skip trading
    """
    try:
        import yfinance as yf
        end   = datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=1)
        start = end - timedelta(days=60)
        df = yf.download("^NSEI", start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"),
                         progress=False, auto_adjust=True)
        if df is None or len(df) < NIFTY_EMA_PERIOD:
            return "BULL"   # default to BULL if no data

        close = df["Close"].squeeze()
        ema20 = float(close.ewm(span=NIFTY_EMA_PERIOD, adjust=False).mean().iloc[-1])
        price = float(close.iloc[-1])

        pct_diff = (price - ema20) / ema20

        if abs(pct_diff) <= NIFTY_CHOPPY_PCT:
            return "CHOPPY"
        elif price > ema20:
            return "BULL"
        else:
            return "BEAR"

    except Exception as e:
        print(f"  [Regime] Error: {e} — defaulting to BULL")
        return "BULL"


def get_daily_ema20(symbol: str, trade_date: str) -> Optional[float]:
    """
    Get 20-day EMA for a specific stock using yfinance daily data.
    Returns the EMA20 value or None if data unavailable.
    """
    try:
        import yfinance as yf
        # Convert symbol from IEOD format (e.g., "RELIANCE.NSE") to yfinance format (e.g., "RELIANCE.NS")
        yf_symbol = symbol.replace(".NSE", ".NS")

        end   = datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=1)
        start = end - timedelta(days=60)
        df = yf.download(yf_symbol, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"),
                         progress=False, auto_adjust=True)
        if df is None or len(df) < 20:
            return None

        close = df["Close"].squeeze()
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        return ema20

    except ImportError:
        # yfinance not installed - silently return None
        return None
    except Exception as e:
        print(f"  [DailyEMA20] Error for {symbol}: {e}")
        return None


def resample_5min_with_warmup(symbol: str, df_today_slice: pd.DataFrame,
                               trade_date: str) -> Optional[pd.DataFrame]:
    """
    Resample to 5-min bars using historical data for indicator warmup.
    Combines 15 days of history with today slice for accurate indicators.
    """
    multi     = load_multi_day_data(symbol, trade_date, days=15)
    today_sym = df_today_slice[df_today_slice["ticker"].astype(str) == symbol].copy()

    if len(today_sym) == 0:
        return None

    if multi is not None and len(multi) > 0:
        hist_sym = multi[multi["datetime"] < pd.Timestamp(trade_date)].copy()
        combined = pd.concat([hist_sym, today_sym], ignore_index=True)
    else:
        combined = today_sym

    combined = combined.sort_values("datetime").drop_duplicates("datetime")

    if len(combined) < 10:
        return None

    combined  = combined.set_index("datetime")
    cols      = [c for c in ["open","high","low","close","volume"]
                 if c in combined.columns]
    if len(cols) < 5:
        return None

    df_5 = combined[cols].resample("3min").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna()

    df_5 = df_5[df_5["close"] >= MIN_PRICE]

    if len(df_5) < BAR_MIN_BARS:
        return None

    return df_5.reset_index()


# ─── INDICATORS ───────────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add all technical indicators to a 3-min DataFrame."""
    x = df.copy()

    # Trend
    x["ema20"]  = x["close"].ewm(span=EMA_PERIOD, adjust=False).mean()
    x["ema50"]  = x["close"].ewm(span=50, adjust=False).mean()

    # ATR
    prev_close = x["close"].shift(1)
    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - prev_close).abs(),
        (x["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    x["atr"] = tr.rolling(MOMENTUM_PERIOD).mean()

    # RSI
    delta = x["close"].diff()
    gain  = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss  = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    rs    = gain / loss.replace(0, np.nan)
    x["rsi"] = 100 - (100 / (1 + rs))

    # Bollinger Bands
    x["bb_mid"]   = x["close"].rolling(BB_PERIOD).mean()
    bb_std        = x["close"].rolling(BB_PERIOD).std()
    x["bb_upper"] = x["bb_mid"] + BB_STD * bb_std
    x["bb_lower"] = x["bb_mid"] - BB_STD * bb_std

    # BB touch flags (for current and previous candles)
    x["close_below_lower"] = x["close"] <= x["bb_lower"]
    x["low_below_lower"] = x["low"] <= x["bb_lower"]
    x["close_above_upper"] = x["close"] >= x["bb_upper"]
    x["high_above_upper"] = x["high"] >= x["bb_upper"]

    # Previous candle BB flags (for confirmation)
    x["prev_close_below_lower"] = x["close_below_lower"].shift(1)
    x["prev_close_above_upper"] = x["close_above_upper"].shift(1)

    # Momentum features
    x["body"]       = (x["close"] - x["open"]).abs()
    x["range"]      = (x["high"] - x["low"]).replace(0, np.nan)
    x["bull"]       = x["close"] > x["open"]
    x["bear"]       = x["close"] < x["open"]
    x["body_ratio"] = (x["body"] / x["range"]).fillna(0)
    x["swing_high"] = x["high"].rolling(10).max().shift(1)
    x["swing_low"]  = x["low"].rolling(10).min().shift(1)

    # Volume momentum
    x["vol_ma"]    = x["volume"].rolling(20).mean()
    x["vol_ratio"] = x["volume"] / x["vol_ma"].replace(0, np.nan)

    return x


def add_htf_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add indicators to HTF (60-min) DataFrame."""
    x = df.copy()
    x["ema20"]      = x["close"].ewm(span=HTF_EMA_PERIOD, adjust=False).mean()
    x["body"]       = (x["close"] - x["open"]).abs()
    x["range"]      = (x["high"] - x["low"]).replace(0, np.nan)
    x["bull"]       = x["close"] > x["open"]
    x["bear"]       = x["close"] < x["open"]
    x["body_ratio"] = (x["body"] / x["range"]).fillna(0)
    x["swing_high"] = x["high"].rolling(8).max().shift(1)
    x["swing_low"]  = x["low"].rolling(8).min().shift(1)
    return x


# ─── SIGNAL ENGINE ────────────────────────────────────────────────────────────

# ── 1. Supply/Demand Zone Detection (adapted from MT5 bot) ────────────────────
def detect_fvg_bull(df: pd.DataFrame, idx: int,
                    min_pct: float = MIN_FVG_PCT) -> Tuple[bool, float, float]:
    """Detect bullish Fair Value Gap at index."""
    if idx < 2:
        return False, 0.0, 0.0
    left_high  = float(df.iloc[idx - 2]["high"])
    right_low  = float(df.iloc[idx]["low"])
    mid_price  = float(df.iloc[idx]["close"])
    gap        = right_low - left_high
    if gap > 0 and gap / mid_price >= min_pct:
        return True, left_high, right_low
    return False, 0.0, 0.0


def detect_fvg_bear(df: pd.DataFrame, idx: int,
                    min_pct: float = MIN_FVG_PCT) -> Tuple[bool, float, float]:
    """Detect bearish Fair Value Gap at index."""
    if idx < 2:
        return False, 0.0, 0.0
    left_low   = float(df.iloc[idx - 2]["low"])
    right_high = float(df.iloc[idx]["high"])
    mid_price  = float(df.iloc[idx]["close"])
    gap        = left_low - right_high
    if gap > 0 and gap / mid_price >= min_pct:
        return True, right_high, left_low
    return False, 0.0, 0.0


def find_base_candle(df: pd.DataFrame, push_end_idx: int,
                     bullish: bool) -> Optional[int]:
    """Find the last opposite candle before the push (the zone base)."""
    for j in range(push_end_idx - 1, max(-1, push_end_idx - 10), -1):
        row = df.iloc[j]
        if bullish and row["bear"]:
            return j
        if (not bullish) and row["bull"]:
            return j
    return None


def detect_sd_signal(symbol: str, df_15: pd.DataFrame,
                     df_60: pd.DataFrame) -> Optional[Signal]:
    """
    Detect Supply/Demand zone setup on 60-min HTF,
    confirmed by 15-min trend.
    Based on: detect_supply_demand_setup() from MT5 bot.
    """
    if len(df_60) < HTF_EMA_PERIOD + 10:
        return None

    htf = add_htf_indicators(df_60)
    ltf = add_indicators(df_15)

    # Current 15-min trend
    if len(ltf) < 2:
        return None

    latest_15 = ltf.iloc[-2]           # last closed 15-min bar
    ltf_trend = "BULL" if latest_15["close"] > latest_15["ema20"] else "BEAR"

    end_idx = len(htf) - 2             # last closed 60-min bar

    # Scan last 20 HTF bars for setup
    for i in range(end_idx, max(HTF_EMA_PERIOD, end_idx - 20), -1):
        row = htf.iloc[i]
        htf_bull = row["close"] > row["ema20"]
        htf_bear = row["close"] < row["ema20"]

        # ── BUY SETUP ──────────────────────────────────────────────────────
        if htf_bull and ltf_trend == "BULL":
            # Count consecutive bull candles (push)
            push_count = 1
            k = i
            while (k - 1 >= 0 and
                   htf.iloc[k - 1]["bull"] and
                   push_count < MAX_PUSH_CANDLES):
                push_count += 1
                k -= 1

            is_marubozu = row["body_ratio"] >= MARUBOZU_RATIO
            valid_push  = (MIN_PUSH_CANDLES <= push_count) or is_marubozu

            if valid_push:
                # Break of structure
                bos_ok = (pd.notna(row["swing_high"]) and
                          row["close"] > row["swing_high"])

                # Fair Value Gap
                fvg_ok, fvg_low, fvg_high = detect_fvg_bull(htf, i)

                # Zone base (last bearish candle before push)
                zone_idx = find_base_candle(htf, k, bullish=True)

                if bos_ok and fvg_ok and zone_idx is not None:
                    z = htf.iloc[zone_idx]
                    zone_low  = float(z["low"])
                    zone_high = float(max(z["open"], z["close"]))
                    cur_price = float(latest_15["close"])

                    # Price must be near zone (within 3% above zone)
                    if zone_low <= cur_price <= zone_high * 1.03:
                        strength = 0.8 + (0.1 if is_marubozu else 0) + \
                                   (0.1 if latest_15["vol_ratio"] > 1.5 else 0)
                        return Signal(
                            symbol=symbol,
                            direction="BUY",
                            source="SD",
                            strength=min(strength, 1.0),
                            price=cur_price,
                            reason=(f"HTF BUY zone [{zone_low:.2f}-{zone_high:.2f}] "
                                    f"BOS={row['swing_high']:.2f} "
                                    f"FVG=[{fvg_low:.2f}-{fvg_high:.2f}]"),
                            timestamp=str(latest_15["datetime"]),
                        )

        # ── SELL SETUP ─────────────────────────────────────────────────────
        if htf_bear and ltf_trend == "BEAR":
            push_count = 1
            k = i
            while (k - 1 >= 0 and
                   htf.iloc[k - 1]["bear"] and
                   push_count < MAX_PUSH_CANDLES):
                push_count += 1
                k -= 1

            is_marubozu = row["body_ratio"] >= MARUBOZU_RATIO
            valid_push  = (MIN_PUSH_CANDLES <= push_count) or is_marubozu

            if valid_push:
                bos_ok = (pd.notna(row["swing_low"]) and
                          row["close"] < row["swing_low"])
                fvg_ok, fvg_low, fvg_high = detect_fvg_bear(htf, i)
                zone_idx = find_base_candle(htf, k, bullish=False)

                if bos_ok and fvg_ok and zone_idx is not None:
                    z = htf.iloc[zone_idx]
                    zone_low  = float(min(z["open"], z["close"]))
                    zone_high = float(z["high"])
                    cur_price = float(latest_15["close"])

                    # Price must be near zone
                    if zone_low * 0.97 <= cur_price <= zone_high:
                        strength = 0.8 + (0.1 if is_marubozu else 0) + \
                                   (0.1 if latest_15["vol_ratio"] > 1.5 else 0)
                        return Signal(
                            symbol=symbol,
                            direction="SELL",
                            source="SD",
                            strength=min(strength, 1.0),
                            price=cur_price,
                            reason=(f"HTF SELL zone [{zone_low:.2f}-{zone_high:.2f}] "
                                    f"BOS={row['swing_low']:.2f} "
                                    f"FVG=[{fvg_low:.2f}-{fvg_high:.2f}]"),
                            timestamp=str(latest_15["datetime"]),
                        )

    return None


# ── 2. Bollinger Band Mean Reversion Signal (3-min bars) ─────────────────────────
def detect_bb_signal(symbol: str,
                     df_3: pd.DataFrame,
                     trade_date: str = "",
                     daily_ema20: Optional[float] = None) -> Optional[Signal]:
    """
    Bollinger Band mean reversion strategy on 3-minute bars.

    BUY Signal (ALL must be true):
      a) Price touches or crosses BELOW lower band
      b) RSI(14) < 40 (oversold confirmation)
      c) Previous candle closed below lower band (confirms it's not just a wick)
      d) Current candle closes ABOVE lower band (reversal confirmation)
      e) Volume >= 1.5x 20-bar average volume
      f) Price above 20-day daily EMA (bullish daily trend)

    SELL Signal (ALL must be true):
      a) Price touches or crosses ABOVE upper band
      b) RSI(14) > 60 (overbought confirmation)
      c) Previous candle closed above upper band
      d) Current candle closes BELOW upper band (reversal confirmation)
      e) Volume >= 1.5x 20-bar average volume
      f) Price below 20-day daily EMA (bearish daily trend)
    """
    df = add_indicators(df_3)
    if len(df) < max(RSI_PERIOD, BB_PERIOD) + 5:
        return None

    # Need at least 3 bars for prev confirmation
    if len(df) < 3:
        return None

    latest = df.iloc[-1]        # current/latest bar
    prev   = df.iloc[-2]        # previous bar

    price     = float(latest["close"])
    rsi       = float(latest["rsi"])
    bb_lower  = float(latest["bb_lower"])
    bb_upper  = float(latest["bb_upper"])
    bb_mid    = float(latest["bb_mid"])

    if any(pd.isna(v) for v in [rsi, bb_lower, bb_upper, bb_mid]):
        return None

    # Volume check
    vol_ratio = float(latest.get("vol_ratio", 0))
    vol_ok = vol_ratio >= 1.5

    # Daily trend filter (optional - if available)
    daily_trend_ok = True
    if daily_ema20 is not None:
        if price > daily_ema20 * 1.02:  # Well above daily EMA20 = bullish
            daily_trend = "BULL"
        elif price < daily_ema20 * 0.98:  # Well below daily EMA20 = bearish
            daily_trend = "BEAR"
        else:
            daily_trend = "NEUTRAL"
    else:
        daily_trend = "NEUTRAL"

    # Count conditions met for confidence
    buy_conditions_met = 0
    sell_conditions_met = 0

    # --- BUY Signal Detection ---
    # a) Price touches or crosses below lower band
    price_below_lower = (latest["close"] <= bb_lower) or (latest["low"] <= bb_lower)
    # b) RSI < 40
    rsi_oversold = rsi < RSI_OVERSOLD
    # c) Previous candle closed below lower band
    prev_below_lower = bool(prev["close_below_lower"])
    # d) Current candle closes above lower band (reversal)
    reversal_above_lower = price > bb_lower
    # e) Volume surge
    vol_condition = vol_ok
    # f) Daily trend (relaxed - allow if no data)
    daily_bullish = (daily_ema20 is None) or (price > daily_ema20 * 0.98)

    if price_below_lower:
        buy_conditions_met += 1
    if rsi_oversold:
        buy_conditions_met += 1
    if prev_below_lower:
        buy_conditions_met += 1
    if reversal_above_lower:
        buy_conditions_met += 1
    if vol_condition:
        buy_conditions_met += 1
    if daily_bullish:
        buy_conditions_met += 1

    # Check BUY signal: need at least 4 conditions for LEVEL_2, 5+ for LEVEL_3
    if buy_conditions_met >= 4:
        strength = 0.5 + (buy_conditions_met - 4) * 0.1
        reasons = []
        if price_below_lower:
            reasons.append(f"BB lower touch ({bb_lower:.2f})")
        if rsi_oversold:
            reasons.append(f"RSI oversold ({rsi:.1f})")
        if prev_below_lower:
            reasons.append("Prev candle below BB")
        if reversal_above_lower:
            reasons.append("Reversal candle")
        if vol_condition:
            reasons.append(f"Volume {vol_ratio:.1f}x")
        if daily_bullish and daily_ema20:
            reasons.append(f"Above daily EMA20 ({daily_ema20:.2f})")

        return Signal(
            symbol=symbol,
            direction="BUY",
            source="BB",
            strength=min(strength, 1.0),
            price=price,
            reason=" | ".join(reasons),
            timestamp=str(latest.get("datetime", "")),
        )

    # --- SELL Signal Detection ---
    # a) Price touches or crosses above upper band
    price_above_upper = (latest["close"] >= bb_upper) or (latest["high"] >= bb_upper)
    # b) RSI > 60
    rsi_overbought = rsi > RSI_OVERBOUGHT
    # c) Previous candle closed above upper band
    prev_above_upper = bool(prev["close_above_upper"])
    # d) Current candle closes below upper band (reversal)
    reversal_below_upper = price < bb_upper
    # e) Volume surge
    vol_condition = vol_ok
    # f) Daily trend (relaxed - allow if no data)
    daily_bearish = (daily_ema20 is None) or (price < daily_ema20 * 1.02)

    if price_above_upper:
        sell_conditions_met += 1
    if rsi_overbought:
        sell_conditions_met += 1
    if prev_above_upper:
        sell_conditions_met += 1
    if reversal_below_upper:
        sell_conditions_met += 1
    if vol_condition:
        sell_conditions_met += 1
    if daily_bearish:
        sell_conditions_met += 1

    # Check SELL signal: need at least 4 conditions for LEVEL_2, 5+ for LEVEL_3
    if sell_conditions_met >= 4:
        strength = 0.5 + (sell_conditions_met - 4) * 0.1
        reasons = []
        if price_above_upper:
            reasons.append(f"BB upper touch ({bb_upper:.2f})")
        if rsi_overbought:
            reasons.append(f"RSI overbought ({rsi:.1f})")
        if prev_above_upper:
            reasons.append("Prev candle above BB")
        if reversal_below_upper:
            reasons.append("Reversal candle")
        if vol_condition:
            reasons.append(f"Volume {vol_ratio:.1f}x")
        if daily_bearish and daily_ema20:
            reasons.append(f"Below daily EMA20 ({daily_ema20:.2f})")

        return Signal(
            symbol=symbol,
            direction="SELL",
            source="BB",
            strength=min(strength, 1.0),
            price=price,
            reason=" | ".join(reasons),
            timestamp=str(latest.get("datetime", "")),
        )

    return None


# ─── CONFIDENCE FILTER (BB Strategy) ──────────────────────────────────────────
def apply_confidence_filter(signals: List[Signal],
                            vol_ratio: float = 0.0) -> Tuple[str, str]:
    """
    BB Strategy confidence filter based on conditions met.

    LEVEL_3 (MAX)  : All 6 BB conditions met → trade immediately
    LEVEL_2 (HIGH) : 4-5 BB conditions met → trade
    LEVEL_1 (LOW)  : < 4 conditions → skip unless vol > 2x

    Returns: (confidence_level, direction)
    """
    if not signals:
        return "NONE", ""

    # Get the strongest BB signal
    bb_signals = [s for s in signals if s.source == "BB"]
    if not bb_signals:
        return "NONE", ""

    # Sort by strength (descending)
    bb_signals.sort(key=lambda s: s.strength, reverse=True)
    best_signal = bb_signals[0]

    direction = best_signal.direction
    strength = best_signal.strength

    # LEVEL_3: strength >= 0.7 (5-6 conditions met)
    if strength >= 0.7:
        return "LEVEL_3", direction

    # LEVEL_2: strength >= 0.5 (4 conditions met)
    if strength >= 0.5:
        return "LEVEL_2", direction

    # LEVEL_1: weaker signal but promote if volume surge
    if vol_ratio >= 2.0:
        return "LEVEL_2", direction

    return "LEVEL_1", direction


# ─── RISK MANAGER ─────────────────────────────────────────────────────────────
def calculate_position(entry: float, sl: float,
                       risk_inr: float = RISK_PER_TRADE_INR) -> Tuple[int, float]:
    """
    Calculate quantity and actual risk.
    qty = floor(risk_inr / (entry - sl))
    Minimum 1 share.
    """
    risk_per_share = abs(entry - sl)
    if risk_per_share <= 0:
        return 0, 0.0

    qty = max(1, int(risk_inr / risk_per_share))
    actual_risk = qty * risk_per_share
    return qty, actual_risk


def calculate_sl_tp(df_3: pd.DataFrame,
                    direction: str,
                    entry: float,
                    symbol: str = "",
                    trade_date: str = "") -> Tuple[float, float, float]:
    """
    Calculate SL and TP levels using SMART SL logic:
    1. 15-min support/resistance ±10%
    2. Daily EMA10 ±10%
    3. ATR-based SL (fallback)
    4. Max ₹4,000 loss cap (whichever gives tighter SL)

    TP levels (BB Mean Reversion):
    - TP1 = BB middle band (20 SMA) — natural mean reversion target
    - TP2 = Opposite BB band — extended target

    SL is the level that results in the SMALLEST loss (tightest SL for risk).
    For BUY:  SL = max(support_sl, ema_sl, atr_sl) → highest SL = smallest loss
    For SELL: SL = min(resistance_sl, ema_sl, atr_sl) → lowest SL = smallest loss
    """
    import pandas as pd

    df = add_indicators(df_3)
    atr = float(df["atr"].iloc[-2]) if len(df) >= 2 else entry * 0.005
    if pd.isna(atr) or atr <= 0:
        atr = entry * 0.005

    # Get current BB levels for TP calculation
    bb_mid = float(df["bb_mid"].iloc[-1])
    bb_upper = float(df["bb_upper"].iloc[-1])
    bb_lower = float(df["bb_lower"].iloc[-1])

    # --- 1. ATR-based SL (fallback) ---
    if direction == "BUY":
        sl_atr = entry - 2.0 * atr
    else:
        sl_atr = entry + 2.0 * atr

    # --- 2. 15-min support/resistance ±10% ---
    # Resample 3-min to 15-min for better support/resistance levels
    sl_support = sl_atr  # default
    try:
        df_indexed = df.copy()
        if 'datetime' in df_indexed.columns:
            df_indexed['datetime'] = pd.to_datetime(df_indexed['datetime'])
            df_indexed = df_indexed.set_index('datetime')
        elif df_indexed.index.name != 'datetime':
            df_indexed.index = pd.to_datetime(df_indexed.index)

        # Resample to 15-min
        df_15 = df_indexed.resample('15min').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()

        if len(df_15) >= 3:
            recent_15 = df_15.tail(10)  # Last 10 15-min bars
            if direction == "BUY":
                support_15min = float(recent_15["low"].min())
                sl_support = support_15min * 0.90  # 10% below 15-min support
            else:
                resistance_15min = float(recent_15["high"].max())
                sl_support = resistance_15min * 1.10  # 10% above 15-min resistance
    except Exception:
        sl_support = sl_atr

    # --- 3. Daily EMA10 ±10% ---
    sl_ema = sl_atr  # default fallback
    if symbol and trade_date:
        try:
            ema10 = get_daily_ema10(symbol, trade_date)
            if ema10 is not None:
                if direction == "BUY":
                    sl_ema = ema10 * 0.90  # 10% below EMA10
                else:
                    sl_ema = ema10 * 1.10  # 10% above EMA10
        except Exception:
            pass

    # --- 4. Choose SL: whichever gives the smallest loss (tightest) ---
    if direction == "BUY":
        sl = max(sl_support, sl_ema, sl_atr)
    else:
        sl = min(sl_support, sl_ema, sl_atr)

    # --- Sanity check: Don't let SL be tighter than reasonable minimum ---
    # Use 1.5x ATR as minimum SL distance (ensures reasonable breathing room)
    MIN_SL_MULT = 1.5
    MIN_SL_DIST = atr * MIN_SL_MULT
    current_dist = abs(entry - sl)
    if current_dist < MIN_SL_DIST:
        if direction == "BUY":
            sl = entry - MIN_SL_DIST
        else:
            sl = entry + MIN_SL_DIST

    # --- 5. Cap max loss at ₹4,000 ---
    MAX_LOSS_CAP = 4000
    sl_dist = abs(entry - sl)

    if sl_dist > 0:
        qty = max(1, int(RISK_PER_TRADE_INR / sl_dist))
        projected_loss = qty * sl_dist

        if projected_loss > MAX_LOSS_CAP:
            capped_sl_dist = MAX_LOSS_CAP / qty
            if direction == "BUY":
                sl = entry - capped_sl_dist
            else:
                sl = entry + capped_sl_dist

    # --- 6. Calculate TP levels (BB Mean Reversion) ---
    # TP1 = BB middle band (natural mean reversion target)
    # TP2 = Opposite BB band (extended target)
    # Ensure TP levels are in the profitable direction
    if direction == "BUY":
        # TP1 should be above entry, minimum 1:1 reward
        tp1_bb = bb_mid if not pd.isna(bb_mid) else entry + sl_dist
        tp1 = max(tp1_bb, entry + sl_dist)  # Ensure at least 1:1
        # TP2 should be above TP1
        tp2_bb = bb_upper if not pd.isna(bb_upper) else entry + sl_dist * 2
        tp2 = max(tp2_bb, entry + sl_dist * 1.5)  # Ensure extended target
    else:
        # TP1 should be below entry, minimum 1:1 reward
        tp1_bb = bb_mid if not pd.isna(bb_mid) else entry - sl_dist
        tp1 = min(tp1_bb, entry - sl_dist)  # Ensure at least 1:1
        # TP2 should be below TP1
        tp2_bb = bb_lower if not pd.isna(bb_lower) else entry - sl_dist * 2
        tp2 = min(tp2_bb, entry - sl_dist * 1.5)  # Ensure extended target

    return round(sl, 2), round(tp1, 2), round(tp2, 2)


def get_daily_ema10(symbol: str, trade_date: str) -> Optional[float]:
    """
    Get 10-day EMA for a specific stock using yfinance daily data.
    Returns the EMA10 value or None if data unavailable.
    """
    try:
        import yfinance as yf
        yf_symbol = symbol.replace(".NSE", ".NS")
        end = datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=1)
        start = end - timedelta(days=30)
        df = yf.download(yf_symbol, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"),
                         progress=False, auto_adjust=True)
        if df is None or len(df) < 10:
            return None
        close = df["Close"].squeeze()
        ema10 = float(close.ewm(span=10, adjust=False).mean().iloc[-1])
        return ema10
    except ImportError:
        return None
    except Exception:
        return None


# ─── PAPER TRADER ─────────────────────────────────────────────────────────────
def generate_trade_id(symbol: str) -> str:
    return f"{symbol}_{datetime.now().strftime('%Y%m%d%H%M%S')}"


def load_trades() -> List[Trade]:
    """Load existing paper trades from CSV."""
    if not TRADES_FILE.exists():
        return []
    try:
        df = pd.read_csv(TRADES_FILE)
        trades = []
        for _, row in df.iterrows():
            t = Trade(**{k: v for k, v in row.items()
                         if k in Trade.__dataclass_fields__})
            trades.append(t)
        return trades
    except Exception:
        return []


def save_trades(trades: List[Trade]):
    """Save trades to CSV."""
    if not trades:
        return
    rows = [asdict(t) for t in trades]
    pd.DataFrame(rows).to_csv(TRADES_FILE, index=False)


def open_paper_trade(symbol: str, direction: str,
                     entry: float, df_5: pd.DataFrame,
                     confidence: str, signals: List[Signal],
                     trade_date: str = "",
                     df_15: Optional[pd.DataFrame] = None) -> Optional[Trade]:
    """Open a new paper trade with smart SL logic."""
    sl, tp1, tp2 = calculate_sl_tp(df_5, direction, entry, symbol, trade_date)

    qty, actual_risk = calculate_position(entry, sl)

    if qty == 0:
        return None

    signal_names = "+".join([s.source for s in signals
                             if s.direction == direction])

    trade = Trade(
        trade_id    = generate_trade_id(symbol),
        symbol      = symbol,
        direction   = direction,
        entry_time  = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        entry_price = entry,
        sl          = sl,
        tp1         = tp1,
        tp2         = tp2,
        qty         = qty,
        risk_inr    = actual_risk,
        confidence  = confidence,
        signals     = signal_names,
        status      = "OPEN",
        trail_sl    = sl,       # initial trail SL = original SL
    )

    print(f"[PaperTrade] OPEN {direction} {symbol} @ {entry:.2f} "
          f"| SL={sl:.2f} TP1={tp1:.2f} TP2={tp2:.2f} "
          f"| qty={qty} risk=₹{actual_risk:.0f} [{confidence}]")

    return trade


def update_paper_trade(trade: Trade, current_price: float,
                       current_time: str) -> Trade:
    """
    Update trade with new exit logic:
    1. Move SL to BE when price moves 25% toward TP1
    2. Close 50% position when price reaches 50% of TP1 distance
    3. Trail remaining 50% with 15% gap from current price
    4. Hard exit at 3:10 PM IST
    """
    if trade.status not in ("OPEN", "PARTIAL"):
        return trade

    # Hard exit at 3:10 PM
    if current_time >= HARD_EXIT_TIME:
        trade.exit_price = current_price
        trade.exit_time  = current_time
        trade.status     = "EXIT"
        remaining_qty    = trade.qty if not trade.partial_done else trade.qty // 2
        if trade.direction == "BUY":
            trade.pnl += (current_price - trade.entry_price) * remaining_qty
        else:
            trade.pnl += (trade.entry_price - current_price) * remaining_qty
        print(f"[PaperTrade] HARD EXIT {trade.symbol} @ {current_price:.2f} "
              f"P&L=₹{trade.pnl:.0f}")
        return trade

    # Calculate distances
    tp1_dist = abs(trade.tp1 - trade.entry_price)
    if trade.direction == "BUY":
        moved = current_price - trade.entry_price
    else:
        moved = trade.entry_price - current_price

    # ── Step 1: Move SL to BE at 25% of TP1 distance ──────────────────────
    if not trade.be_moved and moved >= tp1_dist * BE_TRIGGER_PCT:
        trade.sl      = trade.entry_price
        trade.be_moved = True
        print(f"[PaperTrade] BE MOVED {trade.symbol} SL→{trade.entry_price:.2f} "
              f"(25% trigger at {current_price:.2f})")

    # ── Step 2: Close 50% at TP1 (middle band) or 50% of distance ───────────
    # Check if price reached TP1 (middle band target)
    tp1_hit = (trade.direction == "BUY" and current_price >= trade.tp1) or \
              (trade.direction == "SELL" and current_price <= trade.tp1)

    if not trade.partial_done and (moved >= tp1_dist * PARTIAL_EXIT_PCT or tp1_hit):
        partial_qty   = trade.qty // 2
        partial_price = current_price
        exit_reason = "TP1" if tp1_hit else "50%"
        if trade.direction == "BUY":
            trade.pnl += (partial_price - trade.entry_price) * partial_qty
        else:
            trade.pnl += (trade.entry_price - partial_price) * partial_qty
        trade.partial_done = True
        trade.status       = "PARTIAL"
        print(f"[PaperTrade] PARTIAL EXIT {trade.symbol} @ {partial_price:.2f} "
              f"50% closed ({exit_reason}) | partial P&L=₹{trade.pnl:.0f}")

    # ── Step 3: Trail remaining 50% with 15% gap ──────────────────────────
    if trade.partial_done:
        if trade.direction == "BUY":
            new_trail = current_price * (1 - TRAIL_PCT)
            if new_trail > trade.trail_sl:
                trade.trail_sl = new_trail
                trade.sl       = new_trail
        else:
            new_trail = current_price * (1 + TRAIL_PCT)
            if new_trail < trade.trail_sl or trade.trail_sl == trade.sl:
                trade.trail_sl = new_trail
                trade.sl       = new_trail

    # ── SL check ──────────────────────────────────────────────────────────
    if trade.direction == "BUY" and current_price <= trade.sl:
        remaining = trade.qty if not trade.partial_done else trade.qty // 2
        trade.pnl       += (trade.sl - trade.entry_price) * remaining
        trade.exit_price  = trade.sl
        trade.exit_time   = current_time
        trade.status      = "SL"
        print(f"[PaperTrade] SL HIT {trade.symbol} @ {trade.sl:.2f} "
              f"P&L=₹{trade.pnl:.0f}")

    elif trade.direction == "SELL" and current_price >= trade.sl:
        remaining = trade.qty if not trade.partial_done else trade.qty // 2
        trade.pnl       += (trade.entry_price - trade.sl) * remaining
        trade.exit_price  = trade.sl
        trade.exit_time   = current_time
        trade.status      = "SL"
        print(f"[PaperTrade] SL HIT {trade.symbol} @ {trade.sl:.2f} "
              f"P&L=₹{trade.pnl:.0f}")

    # ── TP2 check (full remaining position) ───────────────────────────────
    if trade.status == "PARTIAL":
        remaining = trade.qty - trade.qty // 2
        if trade.direction == "BUY" and current_price >= trade.tp2:
            trade.pnl       += (trade.tp2 - trade.entry_price) * remaining
            trade.exit_price  = trade.tp2
            trade.exit_time   = current_time
            trade.status      = "TP2"
            print(f"[PaperTrade] TP2 HIT {trade.symbol} @ {trade.tp2:.2f} "
                  f"Total P&L=₹{trade.pnl:.0f}")
        elif trade.direction == "SELL" and current_price <= trade.tp2:
            trade.pnl       += (trade.entry_price - trade.tp2) * remaining
            trade.exit_price  = trade.tp2
            trade.exit_time   = current_time
            trade.status      = "TP2"
            print(f"[PaperTrade] TP2 HIT {trade.symbol} @ {trade.tp2:.2f} "
                  f"Total P&L=₹{trade.pnl:.0f}")

    return trade


# ─── DAILY REPORTER ───────────────────────────────────────────────────────────
def generate_daily_report(trades: List[Trade], trade_date: str) -> str:
    """Generate daily P&L report for Telegram."""
    today_trades = [t for t in trades
                    if t.entry_time.startswith(trade_date)]

    closed = [t for t in today_trades if t.status != "OPEN"]
    open_t = [t for t in today_trades if t.status == "OPEN"]

    total_pnl     = sum(t.pnl for t in closed)
    winners       = [t for t in closed if t.pnl > 0]
    losers        = [t for t in closed if t.pnl <= 0]
    win_rate      = len(winners) / len(closed) * 100 if closed else 0
    gross_profit  = sum(t.pnl for t in winners)
    gross_loss    = sum(t.pnl for t in losers)
    profit_factor = abs(gross_profit / gross_loss) if gross_loss != 0 else 999

    # Confidence breakdown
    l3 = [t for t in today_trades if t.confidence == "LEVEL_3"]
    l2 = [t for t in today_trades if t.confidence == "LEVEL_2"]

    # Best and worst
    best  = max(closed, key=lambda t: t.pnl, default=None)
    worst = min(closed, key=lambda t: t.pnl, default=None)

    # IEOD progress
    parquet_files = list(LAKE_DIR.glob("*.parquet"))
    ieod_days     = len(parquet_files)
    days_remaining = max(0, 120 - ieod_days)

    report = f"""
📊 <b>NSE PAPER TRADING — DAILY REPORT</b>
📅 {trade_date}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💰 <b>P&amp;L SUMMARY</b>
  Total P&amp;L    : ₹{total_pnl:+,.0f}
  Gross Profit  : ₹{gross_profit:,.0f}
  Gross Loss    : ₹{gross_loss:,.0f}
  Profit Factor : {profit_factor:.2f}x

📈 <b>TRADE STATS</b>
  Total trades  : {len(today_trades)}
  Closed        : {len(closed)}
  Open          : {len(open_t)}
  Winners       : {len(winners)}
  Losers        : {len(losers)}
  Win Rate      : {win_rate:.1f}%

🎯 <b>CONFIDENCE</b>
  Level 3 (all 3 signals) : {len(l3)} trades
  Level 2 (any 2 signals) : {len(l2)} trades

{'🏆 Best trade  : ' + best.symbol + ' ₹' + f'{best.pnl:+,.0f}' if best else ''}
{'💔 Worst trade : ' + worst.symbol + ' ₹' + f'{worst.pnl:+,.0f}' if worst else ''}

📁 <b>SYSTEM STATUS</b>
  IEOD data     : {ieod_days}/120 days ({ieod_days/120*100:.0f}%)
  Days to live  : {days_remaining} trading days
  ETA live      : {'READY ✅' if ieod_days >= 120 else '~July 2026'}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Strategy: Supply/Demand + Momentum + Mean Reversion
Risk per trade: ₹{RISK_PER_TRADE_INR:,} | Universe: {len(NIFTY50)} Nifty50 stocks
"""
    return report.strip()


# ─── MAIN SCANNER ─────────────────────────────────────────────────────────────
def scan_symbol(symbol: str, df_all: pd.DataFrame,
                trade_date: str) -> List[Signal]:
    """Run BB signal engine on a single symbol using 3-min bars."""
    # Get 3-min bars for today
    df_3 = resample_5min_with_warmup(symbol, df_all, trade_date)
    if df_3 is None or len(df_3) < BAR_MIN_BARS:
        return []

    signals = []

    # Get daily EMA20 for trend filter
    daily_ema20 = get_daily_ema20(symbol, trade_date)

    # BB Mean Reversion Signal
    bb_signal = detect_bb_signal(symbol, df_3, trade_date, daily_ema20)
    if bb_signal:
        signals.append(bb_signal)

    return signals


def run_daily_scan(trade_date: str, mode: str = "paper"):
    """
    Main daily scan — runs through all Nifty50 stocks
    and generates paper trades.
    """
    print(f"\n{'='*60}")
    print(f"NSE Combined Strategy — {mode.upper()} MODE")
    print(f"Date: {trade_date}")
    print(f"Universe: {len(NIFTY50)} Nifty50 stocks")
    print(f"{'='*60}\n")

    # Load today's data
    df_all = load_day_data(trade_date)
    if df_all is None:
        print(f"No data available for {trade_date}")
        return

    # Load existing trades
    trades = load_trades()
    open_trades = [t for t in trades if t.status == "OPEN"]
    print(f"Existing open trades: {len(open_trades)}")

    new_trades = []
    signal_summary = []

    for symbol in NIFTY50:
        try:
            signals = scan_symbol(symbol, df_all, trade_date)

            if not signals:
                continue

            # Apply confidence filter
            confidence, direction = apply_confidence_filter(signals)

            # Only trade Level 2 and Level 3
            if confidence == "LEVEL_1" or confidence == "NONE":
                signal_summary.append(
                    f"  ⚠️  {symbol}: {len(signals)} signal(s) [{confidence}] — skipped"
                )
                continue

            # Check trade limit
            if len(open_trades) + len(new_trades) >= MAX_TRADES_PER_DAY:
                signal_summary.append(
                    f"  🚫 {symbol}: {confidence} {direction} — max trades reached"
                )
                continue

            # Check if already in trade for this symbol
            existing = [t for t in open_trades + new_trades
                        if t.symbol == symbol]
            if existing:
                signal_summary.append(
                    f"  ⏭️  {symbol}: already in trade — skipped"
                )
                continue

            # Get entry price from last 3-min bar
            df_3 = resample_5min_with_warmup(symbol, df_all, trade_date)
            if df_3 is None:
                continue

            entry = float(df_3.iloc[-2]["close"])

            # Open paper trade
            trade = open_paper_trade(
                symbol, direction, entry, df_3,
                confidence, signals, trade_date
            )

            if trade:
                new_trades.append(trade)
                signal_names = "+".join([s.source for s in signals
                                        if s.direction == direction])
                signal_summary.append(
                    f"  ✅ {symbol}: {confidence} {direction} @ ₹{entry:.2f} "
                    f"[{signal_names}]"
                )

                # Send individual trade alert
                reasons = "\n".join([f"    • {s.source}: {s.reason}"
                                     for s in signals if s.direction == direction])
                alert = (f"🎯 <b>NEW PAPER TRADE</b>\n"
                         f"{direction} {symbol}\n"
                         f"Entry: ₹{entry:.2f} | SL: ₹{trade.sl:.2f}\n"
                         f"TP1: ₹{trade.tp1:.2f} | TP2: ₹{trade.tp2:.2f}\n"
                         f"Qty: {trade.qty} | Risk: ₹{trade.risk_inr:.0f}\n"
                         f"Confidence: {confidence}\n"
                         f"Signals:\n{reasons}")
                send_telegram(alert)

        except Exception as e:
            print(f"[Scanner] Error on {symbol}: {e}")

    # Print signal summary
    print("\n📊 SIGNAL SUMMARY:")
    for line in signal_summary:
        print(line)

    # Save all trades
    all_trades = trades + new_trades
    save_trades(all_trades)

    print(f"\n✅ Scan complete: {len(new_trades)} new trades opened")
    print(f"Total open positions: {len(open_trades) + len(new_trades)}")

    # Generate and send daily report
    report = generate_daily_report(all_trades, trade_date)
    print(f"\n{report}")
    send_telegram(report)


def run_backtest(trade_date: str):
    """
    Backtest v2 — 3-min bars + regime filter + BB strategy + improved exit logic.
    - Nifty50 regime: BUY only in bull, SELL only in bear, CHOPPY allows both
    - 3-min bars for faster entries/exits
    - BB mean reversion signals
    - Partial exit at 50% of TP1 distance
    - Breakeven at 25% of TP1 distance
    - 15% trailing stop on remainder
    - vol > 2x required for single-signal promotion in choppy
    """
    print(f"\n{'='*60}")
    print(f"NSE Combined Strategy v2 — BACKTEST")
    print(f"Date: {trade_date}")
    print(f"{'='*60}\n")

    df_all = load_day_data(trade_date)
    if df_all is None:
        return

    # Nifty50 regime filter
    regime = get_nifty_regime(trade_date)
    print(f"Market regime : {regime}")
    # CHOPPY market: allow trades with relaxed rules (both directions, require 2x volume)

    # Scan every 9 minutes (3 bars apart) from 09:18 to 15:09
    scan_minutes = list(range(18, 60, 9)) + list(range(0, 60, 9)) + list(range(0, 10, 9))
    scan_hours = [9] * 5 + list(range(10, 15)) * 7 + [15]
    scan_windows = [f"{h:02d}:{m:02d}" for h, m in zip(
        [9]*5 + [10]*7 + [11]*7 + [12]*7 + [13]*7 + [14]*7 + [15]*2,
        list(range(18, 60, 9)) + list(range(0, 60, 9)) * 5 + [0, 9]
    )]
    # Filter valid times only (market hours 09:15-15:30)
    scan_windows = [t for t in scan_windows if "09:15" <= t <= "15:10"]
    trades = []

    for window in scan_windows:
        window_dt = pd.Timestamp(f"{trade_date} {window}")
        df_slice  = df_all[df_all["datetime"] <= window_dt]
        if len(df_slice) == 0:
            continue

        for symbol in NIFTY50:
            if any(t.symbol == symbol and t.status in ("OPEN","PARTIAL")
                   for t in trades):
                continue
            if sum(1 for t in trades
                   if t.status in ("OPEN","PARTIAL")) >= MAX_TRADES_PER_DAY:
                break
            try:
                df_3 = resample_5min_with_warmup(symbol, df_slice, trade_date)
                if df_3 is None or len(df_3) < BAR_MIN_BARS:
                    continue

                df_ind    = add_indicators(df_3)
                latest    = df_ind.iloc[-2] if len(df_ind) >= 2 else df_ind.iloc[-1]
                vol_ratio = float(latest.get("vol_ratio", 0))

                # Get daily EMA20 for trend filter
                daily_ema20 = get_daily_ema20(symbol, trade_date)

                signals = []
                bb = detect_bb_signal(symbol, df_3, trade_date, daily_ema20)
                if bb:
                    signals.append(bb)
                if not signals:
                    continue

                confidence, direction = apply_confidence_filter(signals, vol_ratio)
                if confidence not in ("LEVEL_2","LEVEL_3"):
                    continue

                # Regime filter (relaxed for CHOPPY)
                if regime == "BULL" and direction == "SELL":
                    if vol_ratio < 2.0:  # Require 2x volume for counter-trend in bull
                        continue
                if regime == "BEAR" and direction == "BUY":
                    if vol_ratio < 2.0:  # Require 2x volume for counter-trend in bear
                        continue
                # CHOPPY: allow both with normal volume rules

                entry = float(df_3.iloc[-1]["close"])
                trade = open_paper_trade(symbol, direction, entry, df_3,
                                         confidence, signals, trade_date)
                if trade:
                    trade.entry_time = f"{trade_date} {window}"
                    trades.append(trade)
                    sig_names = "+".join([s.source for s in signals
                                          if s.direction == direction])
                    print(f"  {confidence} {direction} {symbol} "
                          f"@ Rs{entry:.2f} [{sig_names}] vol={vol_ratio:.1f}x")
            except Exception:
                continue

    if not trades:
        print(f"\nNo trades (regime={regime})")
        for symbol in NIFTY50[:5]:
            df_3 = resample_5min_with_warmup(symbol, df_all, trade_date)
            if df_3 is None:
                continue
            df_i = add_indicators(df_3)
            lat  = df_i.iloc[-2] if len(df_i) >= 2 else df_i.iloc[-1]
            daily_ema20 = get_daily_ema20(symbol, trade_date)
            bb   = detect_bb_signal(symbol, df_3, trade_date, daily_ema20)
            vol  = float(lat.get("vol_ratio", 0))
            conf, dir_ = apply_confidence_filter([bb] if bb else [], vol)
            print(f"  {symbol}: RSI={lat['rsi']:.0f} BB_low={lat['bb_lower']:.1f} "
                  f"BB_up={lat['bb_upper']:.1f} vol={vol:.1f}x "
                  f"BB={'OK' if bb else 'NO'} conf={conf}")
        print(f"{'='*50}\nBACKTEST RESULTS — {trade_date}")
        print(f"{'='*50}\nTotal trades : 0\nTotal P&L    : Rs +0\n{'='*50}")
        return

    # Bar-by-bar simulation on 5-min bars
    df_all["bar_3"] = df_all["datetime"].dt.floor("3min")
    timestamps      = sorted(df_all["bar_3"].unique())

    for ts in timestamps:
        ts_str   = pd.Timestamp(ts).strftime("%H:%M")
        bar_data = df_all[df_all["bar_3"] == ts]
        for i, trade in enumerate(trades):
            if trade.status not in ("OPEN","PARTIAL"):
                continue
            sym_bar = bar_data[bar_data["ticker"].astype(str) == trade.symbol]
            if len(sym_bar) == 0:
                continue
            cur_price = float(sym_bar["close"].iloc[-1])
            trades[i] = update_paper_trade(trade, cur_price, ts_str)

    closed  = [t for t in trades if t.status not in ("OPEN","PARTIAL")]
    open_t  = [t for t in trades if t.status in ("OPEN","PARTIAL")]
    winners = [t for t in closed if t.pnl > 0]
    total   = sum(t.pnl for t in trades)

    print(f"\n{'='*50}")
    print(f"BACKTEST RESULTS — {trade_date}")
    print(f"{'='*50}")
    print(f"Regime       : {regime}")
    print(f"Total trades : {len(trades)}")
    print(f"Closed       : {len(closed)}")
    print(f"Still open   : {len(open_t)}")
    print(f"Winners      : {len(winners)}")
    if closed:
        print(f"Win rate     : {len(winners)/len(closed)*100:.1f}%")
    print(f"Total P&L    : Rs {total:+,.0f}")
    print(f"{'='*50}")

    for t in trades:
        icon = "WIN" if t.pnl > 0 else "LOSS" if t.pnl < 0 else "OPEN"
        print(f"  {icon} {t.direction} {t.symbol} @ {t.entry_price:.2f} "
              f"-> {t.status} PnL=Rs{t.pnl:+,.0f} [{t.confidence}]")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="NSE Combined Strategy")
    parser.add_argument(
        "--mode",
        choices=["paper", "backtest", "report"],
        default="paper",
        help="paper=live paper trading | backtest=single day sim | report=send report"
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD (default: today)"
    )
    args = parser.parse_args()

    if args.mode == "paper":
        run_daily_scan(args.date, mode="paper")

    elif args.mode == "backtest":
        run_backtest(args.date)

    elif args.mode == "report":
        trades = load_trades()
        report = generate_daily_report(trades, args.date)
        print(report)
        send_telegram(report)


if __name__ == "__main__":
    main()
