#!/usr/bin/env python3
"""
Backtest Mean Reversion Strategy (Versions A & B)
"""

import sys
import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '/root/nse_strategy')

from mean_reversion_strategy import MeanReversionStrategy

# Configuration for mean reversion
MAX_OPEN_TRADES = 3  # Increased from 2
MAX_EXPOSURE_PCT = 0.60  # Increased from 0.50
PORTFOLIO_SIZE = 100_000
MAX_RISK_PER_TRADE = 5_000
FORWARD_BARS = 10  # 2 weeks timeout

class Portfolio:
    """Portfolio tracking"""
    
    def __init__(self):
        self.starting_capital = PORTFOLIO_SIZE
        self.current_equity = PORTFOLIO_SIZE
        self.available_capital = PORTFOLIO_SIZE
        self.total_exposure = 0.0
        self.peak_equity = PORTFOLIO_SIZE
        self.open_positions = []
        self.closed_positions = []
        self.equity_curve = []
        self.skipped_signals = []
        self.trade_count = 0
        self.max_concurrent = 0
        self.max_exposure = 0.0
        
        # Track long/short separately
        self.total_longs = 0
        self.total_shorts = 0
        
    def record_equity(self, date):
        current_open = len(self.open_positions)
        self.max_concurrent = max(self.max_concurrent, current_open)
        self.max_exposure = max(self.max_exposure, self.total_exposure)
        
        longs = len([p for p in self.open_positions if p['direction'] == 'LONG'])
        shorts = len([p for p in self.open_positions if p['direction'] == 'SHORT'])
        
        self.equity_curve.append({
            'date': date.strftime('%Y-%m-%d'),
            'equity': round(self.current_equity, 2),
            'available': round(self.available_capital, 2),
            'exposure': round(self.total_exposure, 2),
            'open': current_open,
            'longs': longs,
            'shorts': shorts
        })
        self.peak_equity = max(self.peak_equity, self.current_equity)
    
    def calculate_position_size(self, entry_price, sl_price):
        sl_amount = abs(entry_price - sl_price)
        if sl_amount == 0:
            return 0
        
        max_qty_risk = int(MAX_RISK_PER_TRADE / sl_amount)
        max_qty_capital = int(self.available_capital / entry_price) if entry_price > 0 else 0
        remaining = (PORTFOLIO_SIZE * MAX_EXPOSURE_PCT) - self.total_exposure
        max_qty_exposure = int(remaining / entry_price) if remaining > 0 else 0
        
        return min(max_qty_risk, max_qty_capital, max_qty_exposure)
    
    def open_position(self, symbol, date, entry_price, qty, sl_price, tp_price, direction, strength=0):
        assert len(self.open_positions) <= MAX_OPEN_TRADES
        
        margin = qty * entry_price
        assert self.total_exposure + margin <= (PORTFOLIO_SIZE * MAX_EXPOSURE_PCT) + 100
        
        if direction == 'LONG':
            self.total_longs += 1
        else:
            self.total_shorts += 1
        
        position = {
            'trade_id': self.trade_count + 1,
            'symbol': symbol,
            'entry_date': date,
            'entry_price': entry_price,
            'qty': qty,
            'direction': direction,
            'sl_price': sl_price,
            'tp_price': tp_price,
            'current_sl': sl_price,
            'highest_price': entry_price if direction == 'LONG' else entry_price,
            'lowest_price': entry_price if direction == 'SHORT' else entry_price,
            'trailing_stage': 0,
            'margin_required': margin,
            'strength': strength,
            'bars_held': 0,
            'status': 'OPEN'
        }
        
        self.open_positions.append(position)
        self.trade_count += 1
        self.available_capital -= margin
        self.total_exposure += margin
        
        return position
    
    def close_position(self, position, exit_price, exit_date, reason):
        if position['direction'] == 'LONG':
            gross_pnl = (exit_price - position['entry_price']) * position['qty']
        else:
            gross_pnl = (position['entry_price'] - exit_price) * position['qty']
        
        brokerage = (position['entry_price'] + exit_price) * position['qty'] * 0.0003
        net_pnl = gross_pnl - brokerage
        
        position['exit_date'] = exit_date
        position['exit_price'] = exit_price
        position['exit_reason'] = reason
        position['net_pnl'] = net_pnl
        position['status'] = 'CLOSED'
        
        self.available_capital += position['margin_required'] + net_pnl
        self.total_exposure -= position['margin_required']
        self.current_equity += net_pnl
        
        self.open_positions.remove(position)
        self.closed_positions.append(position)
    
    def skip_signal(self, symbol, date, reason):
        self.skipped_signals.append({'symbol': symbol, 'date': date.strftime('%Y-%m-%d'), 'reason': reason})


def run_backtest_version(version, data, start_date, end_date, period_name):
    """Run backtest for a specific version"""
    
    period_data = data[(data['Date'] >= start_date) & (data['Date'] <= end_date)].copy()
    
    if len(period_data) == 0:
        return {'error': f'No data for {period_name}'}
    
    portfolio = Portfolio()
    strategy = MeanReversionStrategy(exit_version=version)
    
    # Calculate indicators
    data_with_ind = strategy.calculate_indicators(period_data)
    
    all_dates = sorted(data_with_ind['Date'].unique())
    
    sig_long_total = 0
    sig_short_total = 0
    trades_taken = 0
    
    print(f"\n    Running Version {version} on {len(all_dates)} days...")
    
    for current_date in all_dates:
        current_dt = pd.to_datetime(current_date)
        todays_data = data_with_ind[data_with_ind['Date'] == current_date].copy()
        
        # Check existing positions
        for position in portfolio.open_positions.copy():
            symbol = position['symbol']
            symbol_bar = todays_data[todays_data['Symbol'] == symbol]
            
            if len(symbol_bar) == 0:
                position['bars_held'] += 1
                continue
            
            bar = symbol_bar.iloc[0]
            position['bars_held'] += 1
            
            exit_triggered = False
            exit_price = None
            exit_reason = None
            
            # Check SL/TP
            if position['direction'] == 'LONG':
                if bar['Low'] <= position['sl_price']:
                    exit_price = position['sl_price']
                    exit_reason = 'SL'
                    exit_triggered = True
                elif bar['High'] >= position['tp_price']:
                    exit_price = position['tp_price']
                    exit_reason = 'TARGET'
                    exit_triggered = True
            else:  # SHORT
                if bar['High'] >= position['sl_price']:
                    exit_price = position['sl_price']
                    exit_reason = 'SL'
                    exit_triggered = True
                elif bar['Low'] <= position['tp_price']:
                    exit_price = position['tp_price']
                    exit_reason = 'TARGET'
                    exit_triggered = True
            
            # Version B: RSI exit
            if not exit_triggered and version == 'B':
                rsi_exit = strategy.check_exit_vb(position, bar)
                if rsi_exit:
                    exit_price = bar['Close']  # Exit at close on RSI signal
                    exit_reason = rsi_exit
                    exit_triggered = True
            
            # Timeout
            if not exit_triggered and position['bars_held'] >= FORWARD_BARS:
                exit_price = bar['Close']
                exit_reason = 'TIMEOUT'
                exit_triggered = True
            
            if exit_triggered:
                portfolio.close_position(position, exit_price, current_dt, exit_reason)
                continue
        
        # Generate new signals
        signals = strategy.generate_signals(data_with_ind, current_date)
        sig_long_total += len(signals['LONG'])
        sig_short_total += len(signals['SHORT'])
        
        # Diversification preference
        open_longs = len([p for p in portfolio.open_positions if p['direction'] == 'LONG'])
        open_shorts = len([p for p in portfolio.open_positions if p['direction'] == 'SHORT'])
        
        preferred = []
        if open_longs >= open_shorts:
            preferred = signals['SHORT'] + signals['LONG']
        else:
            preferred = signals['LONG'] + signals['SHORT']
        
        # Open positions
        for signal in preferred:
            if len(portfolio.open_positions) >= MAX_OPEN_TRADES:
                portfolio.skip_signal(signal['symbol'], current_dt, 'max_open')
                continue
            
            symbol = signal['symbol']
            if symbol in [p['symbol'] for p in portfolio.open_positions]:
                portfolio.skip_signal(symbol, current_dt, 'already_holding')
                continue
            
            qty = portfolio.calculate_position_size(signal['entry_price'], signal['sl_price'])
            if qty == 0:
                portfolio.skip_signal(symbol, current_dt, 'qty_zero')
                continue
            
            trade_cost = qty * signal['entry_price']
            if portfolio.total_exposure + trade_cost > PORTFOLIO_SIZE * MAX_EXPOSURE_PCT:
                portfolio.skip_signal(symbol, current_dt, 'max_exposure')
                continue
            
            if trade_cost > portfolio.available_capital:
                portfolio.skip_signal(symbol, current_dt, 'no_capital')
                continue
            
            portfolio.open_position(symbol, current_dt, signal['entry_price'], qty,
                                  signal['sl_price'], signal['tp_price'], 
                                  signal['direction'], signal['signal_strength'])
            trades_taken += 1
        
        portfolio.record_equity(current_dt)
    
    # Close any remaining
    last_date = pd.to_datetime(all_dates[-1])
    last_data = data_with_ind[data_with_ind['Date'] == all_dates[-1]]
    
    for position in portfolio.open_positions.copy():
        sym_bar = last_data[last_data['Symbol'] == position['symbol']]
        exit_px = sym_bar.iloc[0]['Close'] if len(sym_bar) > 0 else position['entry_price']
        portfolio.close_position(position, exit_px, last_date, 'end_of_backtest')
    
    return calculate_metrics(portfolio, sig_long_total, sig_short_total, trades_taken, period_name, version)


def calculate_metrics(portfolio, sig_long, sig_short, trades_taken, period_name, version):
    """Calculate all metrics"""
    
    closed = portfolio.closed_positions
    if len(closed) == 0:
        return {'error': 'No trades', 'version': version, 'period': period_name}
    
    # Exit breakdown
    exit_reasons = {}
    for p in closed:
        r = p.get('exit_reason', 'unknown')
        exit_reasons[r] = exit_reasons.get(r, 0) + 1
    
    # Long/short separation
    longs = [p for p in closed if p['direction'] == 'LONG']
    shorts = [p for p in closed if p['direction'] == 'SHORT']
    
    wins = [p for p in closed if p['net_pnl'] > 0]
    losses = [p for p in closed if p['net_pnl'] <= 0]
    
    long_wins = [p for p in longs if p['net_pnl'] > 0]
    short_wins = [p for p in shorts if p['net_pnl'] > 0]
    
    total_profit = sum(p['net_pnl'] for p in wins)
    total_loss = abs(sum(p['net_pnl'] for p in losses))
    
    win_rate = len(wins) / len(closed) * 100 if closed else 0
    pf = total_profit / total_loss if total_loss > 0 else float('inf')
    
    avg_win = np.mean([p['net_pnl'] for p in wins]) if wins else 0
    avg_loss = np.mean([p['net_pnl'] for p in losses]) if losses else 0
    expectancy = (win_rate/100 * avg_win) - ((100-win_rate)/100 * abs(avg_loss))
    
    # Drawdown
    equity_vals = [e['equity'] for e in portfolio.equity_curve]
    peak = portfolio.starting_capital
    max_dd = 0
    max_dd_pct = 0
    for eq in equity_vals:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = (dd / peak) * 100
    
    # Sharpe
    returns = pd.Series(equity_vals).pct_change().dropna()
    sharpe = (returns.mean() / returns.std()) * np.sqrt(252) if len(returns) > 1 and returns.std() > 0 else 0
    
    # Get 5 example trades
    examples = []
    for p in closed[:5]:
        examples.append({
            'trade_id': p['trade_id'],
            'symbol': p['symbol'],
            'direction': p['direction'],
            'entry_date': p['entry_date'].strftime('%Y-%m-%d') if hasattr(p['entry_date'], 'strftime') else str(p['entry_date']),
            'entry_price': round(p['entry_price'], 2),
            'exit_price': round(p['exit_price'], 2) if p.get('exit_price') else None,
            'exit_reason': p.get('exit_reason', 'unknown'),
            'pnl': round(p['net_pnl'], 2) if 'net_pnl' in p else 0,
            'bars_held': p.get('bars_held', 0)
        })
    
    return {
        'version': version,
        'period': period_name,
        'trading_days': len(portfolio.equity_curve),
        'signals_long': sig_long,
        'signals_short': sig_short,
        'trades_taken': trades_taken,
        'total_trades': len(closed),
        'long_trades': len(longs),
        'short_trades': len(shorts),
        'win_rate': round(win_rate, 2),
        'profit_factor': round(pf, 3),
        'sharpe_ratio': round(sharpe, 3),
        'max_drawdown_pct': round(max_dd_pct, 2),
        'expectancy': round(expectancy, 2),
        'long_win_rate': round(len(long_wins) / len(longs) * 100, 2) if longs else 0,
        'long_avg_pnl': round(np.mean([p['net_pnl'] for p in longs]), 2) if longs else 0,
        'short_win_rate': round(len(short_wins) / len(shorts) * 100, 2) if shorts else 0,
        'short_avg_pnl': round(np.mean([p['net_pnl'] for p in shorts]), 2) if shorts else 0,
        'portfolio_return_pct': round(((portfolio.current_equity / portfolio.starting_capital) - 1) * 100, 2),
        'starting_equity': portfolio.starting_capital,
        'final_equity': round(portfolio.current_equity, 2),
        'exit_reasons': exit_reasons,
        'example_trades': examples,
        'equity_curve_first_5': portfolio.equity_curve[:5],
        'equity_curve_last_5': portfolio.equity_curve[-5:]
    }


# Main execution
print("="*70)
print("MEAN REVERSION STRATEGY BACKTEST")
print("="*70)

# Load data
print("\n[1] Loading data...")
df = pd.read_csv("/root/nse_strategy/data/historical/nifty50_daily_12m.csv")
df['Date'] = pd.to_datetime(df['Date'])

print(f"    Loaded: {len(df):,} rows")

# Define periods
dates = sorted(df['Date'].unique())
period_a_start = dates[0]
period_a_end = dates[int(len(dates) * 0.7) - 1]
period_b_start = dates[int(len(dates) * 0.7)]
period_b_end = dates[-1]

print(f"\n[2] Periods defined:")
print(f"    A: {period_a_start.date()} to {period_a_end.date()}")
print(f"    B: {period_b_start.date()} to {period_b_end.date()}")

# Run both versions
results = {}

for version in ['A', 'B']:
    print(f"\n[3] Running Version {version}...")
    
    res_a = run_backtest_version(version, df, period_a_start, period_a_end, "PERIOD A")
    res_b = run_backtest_version(version, df, period_b_start, period_b_end, "PERIOD B")
    res_full = run_backtest_version(version, df, period_a_start, period_b_end, "COMBINED")
    
    results[f'v{version}'] = {
        'period_a': res_a,
        'period_b': res_b,
        'combined': res_full
    }

# Print comparison
print("\n" + "="*70)
print("VERSION COMPARISON (Combined Periods)")
print("="*70)

for version in ['A', 'B']:
    combined = results[f'v{version}']['combined']
    print(f"\nVersion {version}: {combined.get('period', 'N/A')}")
    
    if 'error' in combined:
        print(f"  ERROR: {combined['error']}")
        continue
    
    print(f"  Trades: {combined['total_trades']} (Long: {combined['long_trades']}, Short: {combined['short_trades']})")
    print(f"  Win Rate: {combined['win_rate']}%")
    print(f"  Profit Factor: {combined['profit_factor']}")
    print(f"  Sharpe: {combined['sharpe_ratio']}")
    print(f"  Max DD: {combined['max_drawdown_pct']}%")
    print(f"  Expectancy: ₹{combined['expectancy']}")
    print(f"  Return: {combined['portfolio_return_pct']}%")
    print(f"  Exits: {combined['exit_reasons']}")

# Gate evaluation for both versions
print("\n" + "="*70)
print("GATE EVALUATION")
print("="*70)

for version in ['A', 'B']:
    combined = results[f'v{version}']['combined']
    
    if 'error' in combined:
        print(f"\nVersion {version}: Error in results")
        continue
    
    print(f"\nVersion {version}:")
    
    gates = [
        ("Win Rate ≥ 45%", combined['win_rate'] >= 45),
        ("PF ≥ 1.3", combined['profit_factor'] >= 1.3),
        ("Sharpe ≥ 1.0", combined['sharpe_ratio'] >= 1.0),
        ("Max DD ≤ 15%", combined['max_drawdown_pct'] <= 15),
        ("Expectancy ≥ ₹50", combined['expectancy'] >= 50)
    ]
    
    passing = sum([g[1] for g in gates])
    print(f"  Gates: {passing}/5")
    
    for name, status in gates:
        print(f"    {'✅' if status else '❌'} {name}")

# Save results
with open('/root/nse_strategy/results_mean_reversion.json', 'w') as f:
    json.dump(results, f, indent=2, default=str)

print(f"\n{'='*70}")
print("Results saved to: /root/nse_strategy/results_mean_reversion.json")
print("="*70)
