#!/usr/bin/env python3
"""
Mean Reversion Strategy Backtest on Nifty 50 IEOD Data (15min -> Daily)
Uses the accumulated 15-minute CSV data for proper Nifty 50 stocks
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
    MIN_AVG_VOLUME, SECTOR_MAPPING, NIFTY50_SYMBOLS, GATE_CRITERIA
)

INTRADAY_FILE = Path("/root/nse_strategy/data/intraday/nifty50_15min.csv")
RESULTS_FILE = Path("/root/nse_strategy/results_mean_reversion_ieod.json")

def load_and_resample_to_daily():
    """Load 15min data and resample to daily."""
    print("="*70)
    print("Loading IEOD 15min Data (Nifty 50)")
    print("="*70)

    if not INTRADAY_FILE.exists():
        print(f"ERROR: {INTRADAY_FILE} not found")
        return None

    # Load 15min data
    df = pd.read_csv(INTRADAY_FILE, parse_dates=['DateTime'])
    df['Date'] = pd.to_datetime(df['DateTime']).dt.date
    df['Date'] = pd.to_datetime(df['Date'])

    print(f"Loaded: {len(df):,} 15-minute bars")
    print(f"Symbols: {df['Symbol'].nunique()}")
    print(f"Date range: {df['Date'].min().date()} to {df['Date'].max().date()}")
    print(f"Trading days: {df['Date'].nunique()}")

    # Resample to daily
    daily = df.groupby(['Symbol', 'Date']).agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).reset_index()

    # Filter for Nifty 50 only
    daily = daily[daily['Symbol'].isin(NIFTY50_SYMBOLS)]

    print(f"\nDaily bars: {len(daily):,}")
    print(f"Nifty 50 symbols: {daily['Symbol'].nunique()}")
    print(f"Date range: {daily['Date'].min().date()} to {daily['Date'].max().date()}")

    return daily

def calculate_indicators(df):
    """Calculate mean reversion indicators."""
    print("\n" + "="*70)
    print("Calculating Indicators")
    print("="*70)

    dfs = []
    min_days = 20  # Need at least 20 days for RSI/ATR calculation

    for symbol in df['Symbol'].unique():
        sym_df = df[df['Symbol'] == symbol].sort_values('Date').copy()

        if len(sym_df) < min_days:
            continue

        # RSI(7) - fast mean reversion
        delta = sym_df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=RSI_PERIOD).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=RSI_PERIOD).mean()
        rs = gain / loss
        sym_df['rsi'] = 100 - (100 / (1 + rs))

        # SMAs (use available data, min periods=1 for SMA200 when <200 days)
        sym_df['sma_200'] = sym_df['Close'].rolling(window=min(SMA_LONG_TERM, len(sym_df)), min_periods=1).mean()
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

        # Volume filter (20-day average)
        sym_df['avg_volume'] = sym_df['Volume'].rolling(window=20, min_periods=5).mean()

        # Signal conditions (Mean Reversion Version B)
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

        # RSI cross for exit (Version B)
        sym_df['rsi_above_50'] = sym_df['rsi'] > RSI_NEUTRAL
        sym_df['rsi_cross_up_50'] = (~sym_df['rsi_above_50'].shift(1).fillna(False)) & sym_df['rsi_above_50']
        sym_df['rsi_cross_down_50'] = (sym_df['rsi_above_50'].shift(1).fillna(True)) & ~sym_df['rsi_above_50']

        dfs.append(sym_df)

    result = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    if len(result) > 0:
        print(f"Calculated indicators for {result['Symbol'].nunique()} symbols")
    else:
        print("WARNING: No symbols passed indicator calculation (insufficient data)")
    print(f"Total rows: {len(result):,}")

    # Check signal frequency
    long_signals = result['long_signal'].sum()
    short_signals = result['short_signal'].sum()
    print(f"Long signals: {long_signals}, Short signals: {short_signals}")

    return result

class Portfolio:
    """Portfolio tracking for backtest."""

    def __init__(self):
        self.starting_capital = PORTFOLIO_SIZE
        self.current_equity = PORTFOLIO_SIZE
        self.open_positions = []
        self.closed_positions = []
        self.daily_pnl = []
        self.signals_seen = 0
        self.signals_taken = 0

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
        self.signals_seen += 1

        # Sector diversification check
        sector = self.get_sector(symbol)
        if sector in self.get_open_sectors():
            return None, "sector_diversification"

        # Max open trades check
        if len(self.open_positions) >= MAX_OPEN_TRADES:
            return None, "max_positions"

        # Calculate position size
        shares = self.calculate_position_size(entry_price, sl_price, self.current_equity)
        if shares <= 0:
            return None, "position_size_zero"

        self.signals_taken += 1
        position = {
            'symbol': symbol,
            'direction': direction,
            'entry_date': date.strftime('%Y-%m-%d'),
            'entry_price': round(entry_price, 2),
            'sl_price': round(sl_price, 2),
            'shares': shares,
            'rsi_at_entry': round(rsi_at_entry, 2),
            'bars_held': 0,
            'sector': sector
        }

        self.open_positions.append(position)
        return position, "opened"

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
            'exit_date': exit_date.strftime('%Y-%m-%d'),
            'exit_price': round(exit_price, 2),
            'exit_reason': exit_reason,
            'net_pnl': round(net_pnl, 2),
            'return_pct': round((net_pnl / (position['entry_price'] * position['shares'])) * 100, 2)
        }

        self.open_positions.remove(position)
        self.closed_positions.append(trade)
        return trade

def run_backtest(df_with_indicators):
    """Run mean reversion backtest."""
    print("\n" + "="*70)
    print("Running Mean Reversion Backtest")
    print("="*70)

    portfolio = Portfolio()
    all_dates = sorted(df_with_indicators['Date'].unique())

    # Skip first 20 days for indicator warmup
    start_idx = 20
    if start_idx >= len(all_dates):
        print("ERROR: Not enough data for indicator warmup")
        return None

    trading_dates = all_dates[start_idx:]
    print(f"Backtesting from {trading_dates[0].date()} to {trading_dates[-1].date()}")
    print(f"Trading days: {len(trading_dates)}\n")

    for i, current_date in enumerate(trading_dates):
        if i % 5 == 0:
            print(f"Day {i+1}/{len(trading_dates)}: {current_date.date()}")

        day_data = df_with_indicators[df_with_indicators['Date'] == current_date].copy()

        # Check exits for open positions
        for position in portfolio.open_positions[:]:  # Copy for safe iteration
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
        skipped_signals = []

        for _, row in day_data.iterrows():
            symbol = row['Symbol']
            if symbol in open_symbols:
                continue

            # Long entry
            if row.get('long_signal', False) and not pd.isna(row['atr']) and row['atr'] > 0:
                entry_price = row['Close']
                atr = row['atr']
                sl_price = entry_price - (ATR_SL_MULT * atr)

                pos, reason = portfolio.open_position(
                    symbol, 'LONG', entry_price, sl_price, row['rsi'], current_date
                )
                if pos is None:
                    skipped_signals.append({'symbol': symbol, 'direction': 'LONG', 'reason': reason})

            # Short entry
            elif row.get('short_signal', False) and not pd.isna(row['atr']) and row['atr'] > 0:
                entry_price = row['Close']
                atr = row['atr']
                sl_price = entry_price + (ATR_SL_MULT * atr)

                pos, reason = portfolio.open_position(
                    symbol, 'SHORT', entry_price, sl_price, row['rsi'], current_date
                )
                if pos is None:
                    skipped_signals.append({'symbol': symbol, 'direction': 'SHORT', 'reason': reason})

        # Record daily P&L
        portfolio.daily_pnl.append({
            'date': current_date.strftime('%Y-%m-%d'),
            'equity': round(portfolio.current_equity, 2),
            'open_positions': len(portfolio.open_positions)
        })

    # Close remaining positions at end
    if portfolio.open_positions:
        print(f"\nClosing {len(portfolio.open_positions)} open positions at end...")
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
        return {'error': 'No trades executed', 'trades': []}

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
        'exit_reasons': exit_reasons,
        'signals_seen': portfolio.signals_seen,
        'signals_taken': portfolio.signals_taken,
        'all_trades': closed
    }

def main():
    print("\n" + "="*70)
    print("Mean Reversion Strategy - IEOD Backtest (Nifty 50)")
    print("="*70)

    # Load data
    daily_data = load_and_resample_to_daily()
    if daily_data is None:
        return

    # Calculate indicators
    df_with_indicators = calculate_indicators(daily_data)

    if len(df_with_indicators) == 0:
        print("\nFAILED: No valid data after indicator calculation")
        return

    # Run backtest
    portfolio = run_backtest(df_with_indicators)

    if portfolio is None:
        print("\nFAILED: Backtest error")
        return

    if not portfolio.closed_positions:
        print("\n" + "="*70)
        print("RESULT: No trades executed during backtest period")
        print("="*70)
        print("\nThis could mean:")
        print("1. No mean reversion signals met the strict criteria")
        print("2. Need more data for SMA200 calculation")
        print("3. Market conditions didn't produce RSI < 25 or > 75 with trend filters")
        print("\nCurrent data range:", daily_data['Date'].min().date(), "to", daily_data['Date'].max().date())
        print("Try extending the backtest period to 60+ days")
        return

    # Calculate metrics
    metrics = calculate_metrics(portfolio)

    # Print results
    print("\n" + "="*70)
    print("BACKTEST RESULTS")
    print("="*70)

    print(f"\n📊 PERFORMANCE METRICS:")
    print(f"  Total Trades: {metrics['total_trades']}")
    print(f"  Win Rate: {metrics['win_rate']}% ({metrics['winning_trades']} wins / {metrics['losing_trades']} losses)")
    print(f"  Profit Factor: {metrics['profit_factor']}")
    print(f"  Expectancy: ₹{metrics['expectancy']}")
    print(f"  Max Drawdown: {metrics['max_drawdown_pct']}%")
    print(f"\n💰 P&L SUMMARY:")
    print(f"  Gross Profit: ₹{metrics['gross_profit']}")
    print(f"  Gross Loss: ₹{metrics['gross_loss']}")
    print(f"  Net P&L: ₹{metrics['net_pnl']} ({metrics['total_return_pct']}%)")
    print(f"  Starting Equity: ₹{metrics['starting_equity']}")
    print(f"  Final Equity: ₹{metrics['final_equity']}")

    print(f"\n📈 EXIT BREAKDOWN:")
    for reason, count in metrics['exit_reasons'].items():
        print(f"  {reason}: {count}")

    print(f"\n📋 SIGNALS:")
    print(f"  Signals Seen: {metrics['signals_seen']}")
    print(f"  Signals Taken: {metrics['signals_taken']}")

    # Gate evaluation
    print("\n" + "="*70)
    print("30-DAY GATE EVALUATION")
    print("="*70)

    gates = [
        ("Win Rate >= 40%", metrics['win_rate'] >= GATE_CRITERIA['min_win_rate'], f"{metrics['win_rate']:.1f}%"),
        ("Profit Factor >= 1.2", metrics['profit_factor'] >= GATE_CRITERIA['min_profit_factor'], f"{metrics['profit_factor']:.2f}"),
        ("Expectancy >= Rs 30", metrics['expectancy'] >= GATE_CRITERIA['min_expectancy'], f"Rs {metrics['expectancy']:.2f}"),
        ("Max Drawdown <= 15%", metrics['max_drawdown_pct'] <= GATE_CRITERIA['max_drawdown_pct'], f"{metrics['max_drawdown_pct']:.1f}%")
    ]

    passed = sum(1 for _, p, _ in gates if p)
    for gate_name, passed_gate, value in gates:
        status = "✅ PASS" if passed_gate else "❌ FAIL"
        print(f"  {status} {gate_name} (Value: {value})")

    print(f"\n{'='*70}")
    if passed >= 4:
        print("✅ OVERALL: ALL GATES PASSED - Ready for live trading!")
    elif passed >= 3:
        print("⚠️  OVERALL: 3/4 gates passed - Consider extending evaluation")
    else:
        print(f"❌ OVERALL: {passed}/4 gates passed - Needs improvement")
    print(f"{'='*70}")

    # Save results
    results = {
        'timestamp': datetime.now().isoformat(),
        'data_source': 'IEOD_15min_Nifty50',
        'trading_days': len(portfolio.daily_pnl),
        'metrics': {k: v for k, v in metrics.items() if k != 'all_trades'},
        'gates_passed': f"{passed}/4",
        'ready_for_live': passed >= 4
    }

    with open(RESULTS_FILE, 'w') as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\nResults saved to: {RESULTS_FILE}")

    # Print top 5 trades
    if metrics['all_trades']:
        print("\n" + "="*70)
        print("TOP 5 TRADES BY P&L")
        print("="*70)
        top_trades = sorted(metrics['all_trades'], key=lambda x: x['net_pnl'], reverse=True)[:5]
        for i, t in enumerate(top_trades, 1):
            emoji = "🟢" if t['net_pnl'] > 0 else "🔴"
            print(f"{i}. {emoji} {t['symbol']} {t['direction']}: +₹{t['net_pnl']:.0f} ({t['return_pct']:.1f}%) | {t['entry_date']} → {t['exit_date']} | {t['exit_reason']}")

if __name__ == "__main__":
    main()
