#!/usr/bin/env python3
"""
Daily Paper Trading Run Script
Mean Reversion Version B with RSI Exit
"""

import json
import time
import logging
import schedule
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import yfinance as yf
import pandas as pd
import numpy as np
from config import (
    PORTFOLIO_SIZE, MAX_RISK_PER_TRADE, MAX_OPEN_TRADES, MAX_EXPOSURE_PCT,
    ATR_SL_MULT, FORWARD_BARS, RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    RSI_NEUTRAL, ADX_THRESHOLD, SMA_LONG_TERM, SMA_SHORT_TERM, MIN_AVG_VOLUME,
    SECTOR_MAPPING, NIFTY50_SYMBOLS, DATA_LOOKBACK_DAYS, YFINANCE_RETRY_COUNT,
    YFINANCE_RETRY_DELAY, POSITIONS_FILE, TRADES_FILE, EQUITY_FILE, LOGS_DIR
)
from telegram_report import send_daily_report, send_error_alert

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(Path(LOGS_DIR) / 'daily_run.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def get_sector_for_symbol(symbol: str) -> str:
    """Get sector for a symbol."""
    for sector, symbols in SECTOR_MAPPING.items():
        if symbol in symbols:
            return sector
    return 'Other'


def download_stock_data(symbol: str, days: int = DATA_LOOKBACK_DAYS) -> Optional[pd.DataFrame]:
    """Download historical stock data with retry logic."""
    for attempt in range(YFINANCE_RETRY_COUNT):
        try:
            ticker = yf.Ticker(f"{symbol}.NS")
            df = ticker.history(period=f"{days}d")
            if len(df) > 0 and not df.empty:
                df = df.reset_index()
                df.columns = [c.lower().replace(' ', '_') for c in df.columns]
                df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
                return df
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed for {symbol}: {e}")
            if attempt < YFINANCE_RETRY_COUNT - 1:
                time.sleep(YFINANCE_RETRY_DELAY)
    
    logger.error(f"Failed to download data for {symbol} after {YFINANCE_RETRY_COUNT} attempts")
    return None


def calculate_rsi(prices: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Calculate RSI indicator."""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr


def calculate_sma(prices: pd.Series, period: int) -> pd.Series:
    """Calculate Simple Moving Average."""
    return prices.rolling(window=period).mean()


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate ADX indicator."""
    plus_dm = df['high'].diff()
    minus_dm = df['low'].diff(-1).abs()
    
    tr1 = df['high'] - df['low']
    tr2 = np.abs(df['high'] - df['close'].shift())
    tr3 = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    atr = tr.rolling(window=period).mean()
    
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    
    plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
    
    dx = (np.abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.rolling(window=period).mean()
    
    return adx


def prepare_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate all required indicators."""
    df = df.copy()
    df['rsi'] = calculate_rsi(df['close'])
    df['atr'] = calculate_atr(df)
    df['sma_200'] = calculate_sma(df['close'], SMA_LONG_TERM)
    df['sma_10'] = calculate_sma(df['close'], SMA_SHORT_TERM)
    df['adx'] = calculate_adx(df)
    df['avg_volume'] = df['volume'].rolling(window=20).mean()
    return df


def load_json_file(filepath: str, default=None) -> any:
    """Load JSON file or return default."""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else []


def save_json_file(filepath: str, data: any):
    """Save data to JSON file."""
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2, default=str)


def get_current_equity() -> float:
    """Get current portfolio equity."""
    equity_data = load_json_file(EQUITY_FILE, [{"date": "2026-03-23", "equity": PORTFOLIO_SIZE}])
    if equity_data:
        return float(equity_data[-1]['equity'])
    return PORTFOLIO_SIZE


def check_entry_conditions(df: pd.DataFrame) -> Optional[Dict]:
    """Check if entry conditions are met for Mean Reversion strategy."""
    if len(df) < SMA_LONG_TERM + 10:
        return None
    
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    
    # Skip if missing indicator values
    if pd.isna(latest['rsi']) or pd.isna(latest['sma_200']) or pd.isna(latest['sma_10']):
        return None
    
    signal = None
    
    # LONG Entry: RSI < 25, Price > SMA200, Price < SMA10, ADX < 30, Volume > 500k
    if (latest['rsi'] < RSI_OVERSOLD and 
        latest['close'] > latest['sma_200'] and 
        latest['close'] < latest['sma_10'] and
        latest['adx'] < ADX_THRESHOLD and
        latest['avg_volume'] >= MIN_AVG_VOLUME):
        signal = 'LONG'
    
    # SHORT Entry: RSI > 75, Price < SMA200, Price > SMA10, ADX < 30, Volume > 500k
    elif (latest['rsi'] > RSI_OVERBOUGHT and 
          latest['close'] < latest['sma_200'] and 
          latest['close'] > latest['sma_10'] and
          latest['adx'] < ADX_THRESHOLD and
          latest['avg_volume'] >= MIN_AVG_VOLUME):
        signal = 'SHORT'
    
    if signal:
        return {
            'signal': signal,
            'price': latest['close'],
            'rsi': latest['rsi'],
            'atr': latest['atr'],
            'sma_200': latest['sma_200'],
            'sma_10': latest['sma_10'],
            'adx': latest['adx'],
            'volume': latest['volume'],
            'avg_volume': latest['avg_volume']
        }
    
    return None


def check_exit_conditions(position: Dict, df: pd.DataFrame) -> Tuple[bool, str, float]:
    """Check if exit conditions are met (Version B - RSI Exit)."""
    if len(df) < 2:
        return False, None, 0
    
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    entry_price = position['entry_price']
    direction = position['direction']
    sl_price = position['sl_price']
    bars_held = position.get('bars_held', 0) + 1
    
    # Check Stop Loss
    if direction == 'LONG':
        if latest['low'] <= sl_price:
            return True, 'SL', sl_price
    else:  # SHORT
        if latest['high'] >= sl_price:
            return True, 'SL', sl_price
    
    # Check RSI Exit (Version B)
    # LONG: exit when RSI crosses above 50
    # SHORT: exit when RSI crosses below 50
    if direction == 'LONG':
        if latest['rsi'] > RSI_NEUTRAL:
            return True, 'RSI_EXIT', latest['close']
    else:  # SHORT
        if latest['rsi'] < RSI_NEUTRAL:
            return True, 'RSI_EXIT', latest['close']
    
    # Check Timeout
    if bars_held >= FORWARD_BARS:
        return True, 'TIMEOUT', latest['close']
    
    return False, None, 0


def get_open_sectors(positions: List[Dict]) -> List[str]:
    """Get list of sectors currently in open positions."""
    sectors = []
    for pos in positions:
        sector = get_sector_for_symbol(pos['symbol'])
        if sector not in sectors:
            sectors.append(sector)
    return sectors


def calculate_position_size(entry_price: float, sl_price: float, available_capital: float, available_exposure: float) -> int:
    """Calculate position size based on risk, exposure, and capital limits."""
    risk_per_share = abs(entry_price - sl_price)
    if risk_per_share <= 0:
        return 0
    
    # Calculate quantity from three constraints
    qty_risk = int(MAX_RISK_PER_TRADE / risk_per_share)
    qty_exposure = int(available_exposure / entry_price)
    qty_capital = int(available_capital / entry_price)
    
    # Take the smallest quantity that satisfies all constraints
    shares = min(qty_risk, qty_exposure, qty_capital)
    
    logger.info(f"Position sizing: qty_risk={qty_risk}, qty_exposure={qty_exposure}, qty_capital={qty_capital}, final={shares}")
    
    return shares


def manage_open_positions(positions: List[Dict], today_data: Dict[str, pd.DataFrame]) -> Tuple[List[Dict], List[Dict]]:
    """Check and manage open positions, return updated positions and closed trades."""
    remaining_positions = []
    closed_trades = []
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    for position in positions:
        symbol = position['symbol']
        if symbol not in today_data or today_data[symbol] is None:
            position['bars_held'] = position.get('bars_held', 0)
            remaining_positions.append(position)
            continue
        
        df = today_data[symbol]
        should_exit, exit_type, exit_price = check_exit_conditions(position, df)
        
        if should_exit:
            # Calculate P&L
            entry_price = position['entry_price']
            shares = position['shares']
            direction = position['direction']
            
            if direction == 'LONG':
                pnl = (exit_price - entry_price) * shares
            else:
                pnl = (entry_price - exit_price) * shares
            
            trade = {
                'symbol': symbol,
                'direction': direction,
                'entry_date': position['entry_date'],
                'exit_date': today_str,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'shares': shares,
                'sl_price': position['sl_price'],
                'bars_held': position.get('bars_held', 0) + 1,
                'exit_type': exit_type,
                'realized_pnl': pnl,
                'realized_pnl_pct': (pnl / (entry_price * shares)) * 100 if entry_price * shares > 0 else 0
            }
            closed_trades.append(trade)
            logger.info(f"Closed position: {symbol} {direction} | Exit: {exit_type} | P&L: ₹{pnl:.2f}")
        else:
            position['bars_held'] = position.get('bars_held', 0) + 1
            position['current_price'] = df.iloc[-1]['close']
            position['current_rsi'] = df.iloc[-1]['rsi']
            remaining_positions.append(position)
    
    return remaining_positions, closed_trades


def generate_signals(symbols: List[str], data: Dict[str, pd.DataFrame], 
                     open_positions: List[Dict]) -> List[Dict]:
    """Generate trading signals for all symbols."""
    signals = []
    open_symbols = {p['symbol'] for p in open_positions}
    open_sectors = get_open_sectors(open_positions)
    current_equity = get_current_equity()
    
    # Calculate current exposure
    total_exposure = sum(p['entry_price'] * p['shares'] for p in open_positions)
    available_exposure = (PORTFOLIO_SIZE * MAX_EXPOSURE_PCT) - total_exposure
    
    for symbol in symbols:
        if symbol in open_symbols:
            continue
        
        if symbol not in data or data[symbol] is None:
            continue
        
        df = data[symbol]
        signal_data = check_entry_conditions(df)
        
        if signal_data:
            sector = get_sector_for_symbol(symbol)
            
            # Sector diversification filter (Fix A)
            if sector in open_sectors:
                signal_data['skip_reason'] = f"Sector {sector} already in portfolio"
                signal_data['skipped'] = True
            else:
                signal_data['skipped'] = False
            
            signal_data['symbol'] = symbol
            signal_data['sector'] = sector
            signals.append(signal_data)
    
    # Sort by RSI (most oversold/overbought first)
    signals.sort(key=lambda x: abs(x['rsi'] - 50), reverse=True)
    
    return signals


def open_new_positions(signals: List[Dict], open_positions: List[Dict], 
                       current_equity: float) -> List[Dict]:
    """Open new paper positions based on signals."""
    new_positions = []
    open_sectors = get_open_sectors(open_positions)
    total_positions = len(open_positions)
    remaining_slots = MAX_OPEN_TRADES - total_positions
    
    if remaining_slots <= 0:
        logger.info("Max open trades reached, skipping new positions")
        for sig in signals:
            sig['taken'] = False
            sig['skip_reason'] = 'Max open trades reached'
        return []
    
    # Calculate exposure
    total_exposure = sum(p['entry_price'] * p['shares'] for p in open_positions)
    available_exposure = (PORTFOLIO_SIZE * MAX_EXPOSURE_PCT) - total_exposure
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    taken_signals = []
    
    for signal in signals:
        if len(new_positions) >= remaining_slots:
            signal['taken'] = False
            signal['skip_reason'] = 'Max positions reached'
            continue
        
        symbol = signal['symbol']
        sector = signal['sector']
        direction = signal['signal']
        
        # Sector check
        if sector in open_sectors or sector in get_open_sectors(open_positions + new_positions):
            signal['taken'] = False
            signal['skip_reason'] = f"Sector {sector} diversification"
            continue
        
        entry_price = signal['price']
        atr = signal['atr']
        
        # Calculate SL
        if direction == 'LONG':
            sl_price = entry_price - (ATR_SL_MULT * atr)
        else:
            sl_price = entry_price + (ATR_SL_MULT * atr)
        
        # Calculate position size
        shares = calculate_position_size(entry_price, sl_price, current_equity, available_exposure)
        position_value = entry_price * shares
        
        # Capital checks
        if position_value > available_exposure:
            signal['taken'] = False
            signal['skip_reason'] = 'Insufficient exposure limit'
            continue
        
        if position_value > current_equity * 0.25:  # Max 25% per position
            signal['taken'] = False
            signal['skip_reason'] = 'Position size exceeds 25% of equity'
            continue
        
        # Create position entry for next day's open
        position = {
            'symbol': symbol,
            'direction': direction,
            'signal_date': today_str,
            'entry_date': None,  # Will be filled on next day's open
            'planned_entry_price': entry_price,
            'entry_price': None,
            'sl_price': sl_price,
            'shares': shares,
            'rsi_at_entry': signal['rsi'],
            'sector': sector,
            'bars_held': 0,
            'status': 'PENDING'  # Pending next day open
        }
        
        new_positions.append(position)
        signal['taken'] = True
        signal['shares'] = shares
        signal['sl_price'] = sl_price
        taken_signals.append(signal)
        
        available_exposure -= position_value
        logger.info(f"New signal: {symbol} {direction} | Entry: ₹{entry_price:.2f} | SL: ₹{sl_price:.2f}")
    
    return new_positions, taken_signals


def update_equity_curve(closed_trades: List[Dict]):
    """Update equity curve with realized P&L."""
    equity_data = load_json_file(EQUITY_FILE, [{"date": "2026-03-23", "equity": PORTFOLIO_SIZE}])
    current_equity = float(equity_data[-1]['equity'])
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    total_pnl = sum(t['realized_pnl'] for t in closed_trades)
    new_equity = current_equity + total_pnl
    
    # Check if entry for today exists
    if equity_data and equity_data[-1]['date'] == today_str:
        equity_data[-1]['equity'] = new_equity
    else:
        equity_data.append({'date': today_str, 'equity': new_equity})
    
    save_json_file(EQUITY_FILE, equity_data)
    logger.info(f"Equity updated: ₹{current_equity:.2f} → ₹{new_equity:.2f}")


def execute_pending_positions(positions: List[Dict], today_data: Dict[str, pd.DataFrame]):
    """Fill pending positions at today's open price."""
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    for position in positions:
        if position.get('status') == 'PENDING':
            symbol = position['symbol']
            if symbol in today_data and today_data[symbol] is not None:
                df = today_data[symbol]
                if len(df) > 0:
                    # Fill at today's open
                    open_price = df.iloc[0]['open']
                    position['entry_price'] = open_price
                    position['entry_date'] = today_str
                    position['status'] = 'OPEN'
                    logger.info(f"Filled position: {symbol} at ₹{open_price:.2f}")


def calculate_running_metrics(closed_trades: List[Dict]) -> Dict:
    """Calculate running performance metrics."""
    if not closed_trades:
        return {
            'total_trades': 0,
            'win_rate': 0,
            'profit_factor': 0,
            'expectancy': 0,
            'max_drawdown': 0,
            'return_pct': 0
        }
    
    total_trades = len(closed_trades)
    winning_trades = [t for t in closed_trades if t['realized_pnl'] > 0]
    losing_trades = [t for t in closed_trades if t['realized_pnl'] <= 0]
    
    win_rate = len(winning_trades) / total_trades * 100 if total_trades > 0 else 0
    
    gross_profit = sum(t['realized_pnl'] for t in winning_trades)
    gross_loss = abs(sum(t['realized_pnl'] for t in losing_trades))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    expectancy = sum(t['realized_pnl'] for t in closed_trades) / total_trades if total_trades > 0 else 0
    
    # Calculate drawdown
    equity_data = load_json_file(EQUITY_FILE, [{"date": "2026-03-23", "equity": PORTFOLIO_SIZE}])
    if len(equity_data) > 1:
        equities = [float(e['equity']) for e in equity_data]
        peak = equities[0]
        max_dd = 0
        for eq in equities:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd
    else:
        max_dd = 0
    
    current_equity = get_current_equity()
    return_pct = (current_equity - PORTFOLIO_SIZE) / PORTFOLIO_SIZE * 100
    
    return {
        'total_trades': total_trades,
        'win_rate': round(win_rate, 1),
        'profit_factor': round(profit_factor, 2),
        'expectancy': round(expectancy, 2),
        'max_drawdown': round(max_dd, 2),
        'return_pct': round(return_pct, 2)
    }


def is_trading_day() -> bool:
    """Check if today is a trading day (not weekend)."""
    today = datetime.now()
    # Saturday = 5, Sunday = 6
    if today.weekday() >= 5:
        return False
    return True


def run_daily_trading():
    """Main daily trading routine."""
    logger.info("="*60)
    logger.info("Starting Daily Paper Trading Run")
    logger.info("="*60)
    
    # Check if trading day
    if not is_trading_day():
        logger.info("Market closed - Weekend")
        send_error_alert("Market closed - Weekend", is_error=False)
        return
    
    # Load existing state
    positions = load_json_file(POSITIONS_FILE, [])
    trades = load_json_file(TRADES_FILE, [])
    
    logger.info(f"Loaded {len(positions)} open positions")
    
    # Download data for all symbols
    logger.info("Downloading market data...")
    data = {}
    failed_symbols = []
    
    for symbol in NIFTY50_SYMBOLS:
        df = download_stock_data(symbol)
        if df is not None:
            df = prepare_indicators(df)
            data[symbol] = df
        else:
            failed_symbols.append(symbol)
        time.sleep(0.5)  # Rate limiting
    
    if len(failed_symbols) > 10:
        logger.error(f"Too many failed downloads ({len(failed_symbols)}), skipping today")
        send_error_alert(f"Data download failed - {len(failed_symbols)} symbols failed")
        return
    
    # Execute pending positions (fill at today's open)
    execute_pending_positions(positions, data)
    
    # Manage open positions (check exits)
    closed_today = []
    positions, closed = manage_open_positions(positions, data)
    closed_today.extend(closed)
    trades.extend(closed)
    
    if closed:
        logger.info(f"Closed {len(closed)} positions today")
        update_equity_curve(closed)
    
    # Generate new signals
    all_signals = generate_signals(NIFTY50_SYMBOLS, data, positions)
    
    # Open new positions
    new_positions, taken_signals = open_new_positions(all_signals, positions, get_current_equity())
    positions.extend(new_positions)
    
    # Calculate metrics
    metrics = calculate_running_metrics(trades)
    
    # Save state
    save_json_file(POSITIONS_FILE, positions)
    save_json_file(TRADES_FILE, trades)
    
    logger.info(f"Saved {len(positions)} open positions")
    logger.info(f"Total trades: {metrics['total_trades']}")
    logger.info(f"Win rate: {metrics['win_rate']}%")
    logger.info(f"Return: {metrics['return_pct']}%")
    
    # Prepare report data
    report_data = {
        'open_positions': positions,
        'closed_today': closed_today,
        'signals': all_signals,
        'taken_signals': taken_signals,
        'metrics': metrics,
        'equity': get_current_equity(),
        'failed_symbols': failed_symbols
    }
    
    # Send Telegram report
    send_daily_report(report_data)
    
    logger.info("Daily run completed")


def run_on_schedule():
    """Run trading schedule continuously."""
    # Schedule for 9:30 AM (after market open to fill pending orders)
    schedule.every().day.at("09:35").do(run_daily_trading)
    
    # Also run at market close to process exits
    schedule.every().day.at("15:35").do(run_daily_trading)
    
    logger.info("Paper trading scheduler started")
    logger.info("Scheduled runs: 09:35, 15:35")
    
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--now":
        run_daily_trading()
    else:
        run_on_schedule()
