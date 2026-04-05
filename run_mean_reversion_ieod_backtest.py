#!/usr/bin/env python3
"""
Mean Reversion Strategy Backtest on IEOD Data
Converts 1-minute intraday data to daily bars and runs backtest
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import json
import sys

sys.path.insert(0, '/root/nse_strategy/paper_trading')
from config import (
    PORTFOLIO_SIZE, MAX_RISK_PER_TRADE, MAX_OPEN_TRADES, MAX_EXPOSURE_PCT,
    ATR_SL_MULT, ATR_TP_MULT, FORWARD_BARS, RSI_PERIOD, RSI_OVERSOLD,
    RSI_OVERBOUGHT, RSI_NEUTRAL, ADX_THRESHOLD, SMA_LONG_TERM, SMA_SHORT_TERM,
    MIN_AVG_VOLUME, SECTOR_MAPPING, NIFTY50_SYMBOLS, SYMBOL_MAP
)

# Paths
LAKE_DIR = Path("/root/nse_strategy/data/lake")
RESULTS_FILE = Path("/root/nse_strategy/results_mean_reversion_ieod.json")

def load_all_ieod_data():
    """Load all IEOD parquet files and convert to daily."""
    all_files = sorted(LAKE_DIR.glob("*.parquet"))

    if not all_files:
        print("No IEOD data found in data/lake/")
        return None

    print(f"Loading {len(all_files)} trading days of IEOD data...")

    all_data = []
    for f in all_files:
        try:
            df = pd.read_parquet(f)
            all_data.append(df)
        except Exception as e:
            print(f"  Error loading {f}: {e}")

    if not all_data:
        return None

    combined = pd.concat(all_data, ignore_index=True)
    print(f"  Total rows loaded: {len(combined):,}")
    return combined

def convert_to_daily(df):
    """Convert 1-minute IEOD data to daily OHLCV."""
    print("\nConverting IEOD to daily bars...")

    # Clean ticker names
    df['Symbol'] = df['ticker'].str.replace('.BE.NSE', '', regex=False)
    df['Symbol'] = df['Symbol'].str.replace('.EQ.NSE', '', regex=False)

    # Parse datetime
    df['DateTime'] = pd.to_datetime(df['DateTime'])
    df['Date'] = pd.to_datetime(df['trade_date']).dt.date

    # Resample to daily
    daily = df.groupby(['Symbol', 'Date']).agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).reset_index()

    daily['Date'] = pd.to_datetime(daily['Date'])

    print(f"  Daily bars: {len(daily):,}")
    print(f"  Symbols: {daily['Symbol'].nunique()}")
    print(f"  Date range: {daily['Date'].min().date()} to {daily['Date'].max().date()}")

    return daily

def calculate_indicators(df):
    """Calculate mean reversion indicators."""
    print("\nCalculating indicators...")
    print(f"  Note: Using available {df['Date'].nunique()} days (SMA200 requires warm-up)")

    dfs = []
    for symbol in df['Symbol'].unique():
        sym_df = df[df['Symbol'] == symbol].sort_values('Date').copy()

        # Require at least 30 days of data (reduced from 210 for this test)
        if len(sym_df) < 30:
            continue

        # RSI(7)
        delta = sym_df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=RSI_PERIOD).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=RSI_PERIOD).mean()
        rs = gain / loss
        sym_df['rsi'] = 100 - (100 / (1 + rs))

        # SMAs
        sym_df['sma_200'] = sym_df['Close'].rolling(window=SMA_LONG_TERM).mean()
        sym_df['sma_10'] = sym_df['Close'].rolling(window=SMA_SHORT_TERM).mean()

        # ATR(14)
        tr1 = sym_df['High'] - sym_df['Low']
        tr2 = abs(sym_df['High'] - sym_df['Close'].shift(1))
        tr3 = abs(sym_df['Low'] - sym_df['Close'].shift(1))
        sym_df['tr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        sym_df['atr'] = sym_df['tr'].rolling(window=14).mean()

        # ADX (simplified)
        sym_df['plus_dm'] = np.where(
            (sym_df['High'] - sym_df['High'].shift(1)) > (sym_df['Low'].shift(1) - sym_df['Low']),
            np.maximum(sym_df['High'] - sym_df['High'].shift(1), 0), 0
        )
        sym_df['minus_dm'] = np.where(
            (sym_df['Low'].shift(1) - sym_df['Low']) > (sym_df['High'] - sym_df['High'].shift(1)),
            np.maximum(sym_df['Low'].shift(1) - sym_df['Low'], 0), 0
        )
        atr = sym_df['tr'].rolling(14).mean()
        plus_di = 100 * sym_df['plus_dm'].rolling(14).mean() / (atr + 0.001)
        minus_di = 100 * sym_df['minus_dm'].rolling(14).mean() / (atr + 0.001)
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 0.001)) * 100
        sym_df['adx'] = dx.rolling(14).mean()

        # Volume filter
        sym_df['avg_volume'] = sym_df['Volume'].rolling(window=20).mean()

        # Signal conditions
        sym_df['long_signal'] = (
            (sym_df['rsi'] < RSI_OVERSOLD) &
            (sym_df['Close'] > sym_df['sma_200']) &
            (sym_df['Close'] < sym_df['sma_10']) &
            (sym_df['adx'] < ADX_THRESHOLD) &
            (sym_df['avg_volume'] >= MIN_AVG_VOLUME)
        )

        sym_df['short_signal'] = (
            (sym_df['rsi'] > RSI_OVERBOUGHT) &
            (sym_df['Close'] < sym_df['sma_200']) &
            (sym_df['Close'] > sym_df['sma_10']) &
            (sym_df['adx'] < ADX_THRESHOLD) &
            (sym_df['avg_volume'] >= MIN_AVG_VOLUME)
        )

        # RSI cross for exit
        sym_df['rsi_above_50'] = sym_df['rsi'] > RSI_NEUTRAL
        sym_df['rsi_cross_up_50'] = (~sym_df['rsi_above_50'].shift(1).fillna(False)) & sym_df['rsi_above_50']
        sym_df['rsi_cross_down_50'] = (sym_df['rsi_above_50'].shift(1).fillna(True)) & ~sym_df['rsi_above_50']

        dfs.append(sym_df)

    result = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    print(f"  Calculated indicators for {result['Symbol'].nunique()} symbols")
    return result

class Portfolio:
    """Portfolio tracking for backtest."""

    def __init__(self):
        self.starting_capital = PORTFOLIO_SIZE
        self.current_equity = PORTFOLIO_SIZE
        self.open_positions = []
        self.closed_positions = []
        self.daily_pnl = []

    def get_sector(self, symbol):
        for sector, symbols in SECTOR_MAPPING.items():
            if symbol in symbols:
                return sector
        return 'Other'

    def get_open_sectors(self):
        return [self.get_sector(p['symbol']) for p in self.open_positions]

    def calculate_position_size(self, entry_price, sl_price, current_equity):
        risk_per_share = abs(entry_price - sl_price)
        if risk_per_share <= 0:
            return 0

        qty_risk = int(MAX_RISK_PER_TRADE / risk_per_share)
        qty_capital = int(current_equity / entry_price)

        total_exposure = sum(p['entry_price'] * p['shares'] for p in self.open_positions)
        remaining_exposure = (PORTFOLIO_SIZE * MAX_EXPOSURE_PCT) - total_exposure
        qty_exposure = int(remaining_exposure / entry_price) if remaining_exposure > 0 else 0

        return min(qty_risk, qty_exposure, qty_capital)

    def open_position(self, symbol, direction, entry_price, sl_price, rsi_at_entry, date):
        if len(self.open_positions) >= MAX_OPEN_TRADES:
            return None

        sector = self.get_sector(symbol)
        if sector in self.get_open_sectors():
            return None

        shares = self.calculate_position_size(entry_price, sl_price, self.current_equity)
        if shares <= 0:
            return None

        position = {
            'symbol': symbol,
            'direction': direction,
            'entry_date': date,
            'entry_price': entry_price,
            'sl_price': sl_price,
            'shares': shares,
            'rsi_at_entry': rsi_at_entry,
            'bars_held': 0,
            'sector': sector
        }

        self.open_positions.append(position)
        return position

    def close_position(self, position, exit_price, exit_date, exit_reason):
        if position['direction'] == 'LONG':
            pnl = (exit_price - position['entry_price']) * position['shares']
        else:
            pnl = (position['entry_price'] - exit_price) * position['shares']

        # Apply brokerage (0.03% per side)
        brokerage = (position['entry_price'] + exit_price) * position['shares'] * 0.0003
        net_pnl = pnl - brokerage

        self.current_equity += net_pnl

        trade = {
            **position,
            'exit_date': exit_date,
            'exit_price': exit_price,
            'exit_reason': exit_reason,
            'net_pnl': net_pnl,
            'return_pct': (net_pnl / (position['entry_price'] * position['shares'])) * 100
        }

        self.open_positions.remove(position)
        self.closed_positions.append(trade)
        return trade

def run_backtest(df_with_indicators):
    """Run mean reversion backtest."""
    print("\n" + "="*70)
    print("Running Mean Reversion Backtest on IEOD Data")
    print("="*70)

    portfolio = Portfolio()
    all_dates = sorted(df_with_indicators['Date'].unique())

    # Skip first 30 days for indicator calculation warmup
    start_idx = 30
    if start_idx >= len(all_dates):
        print("ERROR: Not enough data for indicator warmup period")
        return None

    trading_dates = all_dates[start_idx:]
    print(f"\nBacktesting from {trading_dates[0].date()} to {trading_dates[-1].date()}")
    print(f"Total trading days: {len(trading_dates)}")

    for i, current_date in enumerate(trading_dates):
        if i % 10 == 0:
            print(f"  Processing day {i+1}/{len(trading_dates)}: {current_date.date()}")

        day_data = df_with_indicators[df_with_indicators['Date'] == current_date].copy()

        # Check exits for open positions
        for position in portfolio.open_positions[:]:  # Copy to avoid modification during iteration
            symbol = position['symbol']
            symbol_data = day_data[day_data['Symbol'] == symbol]

            if len(symbol_data) == 0:
                position['bars_held'] += 1
                continue

            bar = symbol_data.iloc[0]
            position['bars_held'] += 1

            # Check stop loss
            if position['direction'] == 'LONG':
                if bar['Low'] <= position['sl_price']:
                    portfolio.close_position(position, position['sl_price'], current_date, 'SL')
                    continue
            else:
                if bar['High'] >= position['sl_price']:
                    portfolio.close_position(position, position['sl_price'], current_date, 'SL')
                    continue

            # Check RSI exit (Version B)
            if position['direction'] == 'LONG':
                if bar['rsi_cross_up_50'] or bar['rsi'] > RSI_NEUTRAL:
                    portfolio.close_position(position, bar['Close'], current_date, 'RSI_EXIT')
                    continue
            else:
                if bar['rsi_cross_down_50'] or bar['rsi'] < RSI_NEUTRAL:
                    portfolio.close_position(position, bar['Close'], current_date, 'RSI_EXIT')
                    continue

            # Check timeout
            if position['bars_held'] >= FORWARD_BARS:
                portfolio.close_position(position, bar['Close'], current_date, 'TIMEOUT')
                continue

        # Generate new signals
        open_symbols = {p['symbol'] for p in portfolio.open_positions}

        for _, row in day_data.iterrows():
            symbol = row['Symbol']
            if symbol in open_symbols:
                continue

            if row.get('long_signal', False):
                entry_price = row['Close']
                atr = row['atr']
                sl_price = entry_price - (ATR_SL_MULT * atr)

                portfolio.open_position(
                    symbol, 'LONG', entry_price, sl_price, row['rsi'], current_date
                )

            elif row.get('short_signal', False):
                entry_price = row['Close']
                atr = row['atr']
                sl_price = entry_price + (ATR_SL_MULT * atr)

                portfolio.open_position(
                    symbol, 'SHORT', entry_price, sl_price, row['rsi'], current_date
                )

        # Record daily P&L
        portfolio.daily_pnl.append({
            'date': current_date.strftime('%Y-%m-%d'),
            'equity': portfolio.current_equity,
            'open_positions': len(portfolio.open_positions)
        })

    # Close remaining positions at end
    if portfolio.open_positions:
        last_date = trading_dates[-1]
        last_data = df_with_indicators[df_with_indicators['Date'] == last_date]

        for position in portfolio.open_positions[:]:
            symbol = position['symbol']
            symbol_bar = last_data[last_data['Symbol'] == symbol]
            exit_price = symbol_bar.iloc[0]['Close'] if len(symbol_bar) > 0 else position['entry_price']
            portfolio.close_position(position, exit_price, last_date, 'END_OF_TEST')

    return portfolio

def calculate_metrics(portfolio):
    """Calculate backtest metrics."""
    closed = portfolio.closed_positions

    if not closed:
        return {'error': 'No trades executed'}

    wins = [t for t in closed if t['net_pnl'] > 0]
    losses = [t for t in closed if t['net_pnl'] <= 0]

    total_profit = sum(t['net_pnl'] for t in wins)
    total_loss = abs(sum(t['net_pnl'] for t in losses))

    win_rate = len(wins) / len(closed) * 100 if closed else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
    expectancy = sum(t['net_pnl'] for t in closed) / len(closed) if closed else 0

    # Max drawdown
    equities = [portfolio.starting_capital] + [d['equity'] for d in portfolio.daily_pnl]
    peak = portfolio.starting_capital
    max_dd = 0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd

    total_return = ((portfolio.current_equity - portfolio.starting_capital) / portfolio.starting_capital) * 100

    # Exit reasons
    exit_reasons = {}
    for t in closed:
        reason = t['exit_reason']
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    return {
        'total_trades': len(closed),
        'winning_trades': len(wins),
        'losing_trades': len(losses),
        'win_rate': round(win_rate, 2),
        'profit_factor': round(profit_factor, 3),
        'expectancy': round(expectancy, 2),
        'max_drawdown_pct': round(max_dd, 2),
        'gross_profit': round(total_profit, 2),
        'gross_loss': round(total_loss, 2),
        'net_pnl': round(portfolio.current_equity - portfolio.starting_capital, 2),
        'total_return_pct': round(total_return, 2),
        'starting_equity': portfolio.starting_capital,
        'final_equity': round(portfolio.current_equity, 2),
        'exit_reasons': exit_reasons
    }

def main():
    print("="*70)
    print("Mean Reversion Strategy - IEOD Backtest")
    print("="*70)

    # Load IEOD data
    ieod_data = load_all_ieod_data()
    if ieod_data is None:
        print("\nFAILED: No IEOD data available")
        return

    # Convert to daily
    daily_data = convert_to_daily(ieod_data)

    # Calculate indicators
    df_with_indicators = calculate_indicators(daily_data)

    if len(df_with_indicators) == 0:
        print("\nFAILED: No valid data after indicator calculation")
        return

    # Run backtest
    portfolio = run_backtest(df_with_indicators)

    if portfolio is None or not portfolio.closed_positions:
        print("\nNo trades executed during backtest period")
        return

    # Calculate metrics
    metrics = calculate_metrics(portfolio)

    # Print results
    print("\n" + "="*70)
    print("BACKTEST RESULTS")
    print("="*70)

    for key, value in metrics.items():
        if isinstance(value, dict):
            print(f"\n{key}:")
            for k, v in value.items():
                print(f"  {k}: {v}")
        else:
            print(f"{key}: {value}")

    # Gate evaluation
    print("\n" + "="*70)
    print("30-DAY GATE EVALUATION")
    print("="*70)

    from config import GATE_CRITERIA

    gates = [
        ("Win Rate >= 40%", metrics['win_rate'] >= GATE_CRITERIA['min_win_rate'], f"{metrics['win_rate']}%"),
        ("Profit Factor >= 1.2", metrics['profit_factor'] >= GATE_CRITERIA['min_profit_factor'], f"{metrics['profit_factor']}"),
        ("Expectancy >= Rs 30", metrics['expectancy'] >= GATE_CRITERIA['min_expectancy'], f"Rs {metrics['expectancy']}"),
        ("Max Drawdown <= 15%", metrics['max_drawdown_pct'] <= GATE_CRITERIA['max_drawdown_pct'], f"{metrics['max_drawdown_pct']}%")
    ]

    passed = sum(1 for _, p, _ in gates if p)
    for gate_name, passed_gate, value in gates:
        status = "✅ PASS" if passed_gate else "❌ FAIL"
        print(f"{status}: {gate_name} (Value: {value})")

    print(f"\nOverall: {passed}/4 gates passed")
    print(f"Status: {'✅ READY FOR LIVE' if passed >= 4 else '⏳ NEEDS IMPROVEMENT'}")

    # Save results
    results = {
        'timestamp': datetime.now().isoformat(),
        'data_source': 'IEOD_1min_to_daily',
        'trading_days': len(portfolio.daily_pnl),
        'metrics': metrics,
        'gates_passed': f"{passed}/4",
        'trades': portfolio.closed_positions
    }

    with open(RESULTS_FILE, 'w') as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\nResults saved to: {RESULTS_FILE}")

if __name__ == "__main__":
    main()
