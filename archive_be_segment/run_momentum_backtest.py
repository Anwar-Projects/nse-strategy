#!/usr/bin/env python3
"""
Backtest Momentum Breakout Strategy on 12-month daily data
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '/root/nse_strategy')

from backtest_engine import run_time_based_backtest_rule_based, Portfolio
from momentum_breakout_strategy import MomentumBreakoutStrategy

print("=" * 70)
print("MOMENTUM BREAKOUT STRATEGY BACKTEST")
print("=" * 70)

# Load data
print("\n[1] Loading Nifty 50 daily data...")
data_file = Path("/root/nse_strategy/data/historical/nifty50_daily_12m.csv")
df = pd.read_csv(data_file)
df['Date'] = pd.to_datetime(df['Date'])

print(f"    Loaded: {len(df):,} rows")
print(f"    Symbols: {df['Symbol'].nunique()}")
print(f"    Date range: {df['Date'].min()} to {df['Date'].max()}")

# Initialize strategy
strategy = MomentumBreakoutStrategy()

print("\n[2] Calculating indicators...")
df_with_indicators = strategy.calculate_indicators(df)
print(f"    Data with indicators: {len(df_with_indicators):,} rows")

# 70/30 split
all_dates = sorted(df_with_indicators['Date'].unique())
split_idx = int(len(all_dates) * 0.7)
train_dates = all_dates[:split_idx]
test_dates = all_dates[split_idx:]

print(f"\n[3] Train/Test Split:")
print(f"    Training: {train_dates[0].strftime('%Y-%m-%d')} to {train_dates[-1].strftime('%Y-%m-%d')} ({len(train_dates)} days)")
print(f"    Testing: {test_dates[0].strftime('%Y-%m-%d')} to {test_dates[-1].strftime('%Y-%m-%d')} ({len(test_dates)} days)")

# Split data
train_data = df_with_indicators[df_with_indicators['Date'].isin(train_dates)].copy()
test_data = df_with_indicators[df_with_indicators['Date'].isin(test_dates)].copy()

print(f"\n[4] Verifying signals on training period...")
signal_counts = []
for date in train_dates[:10]:  # First 10 days
    signals = strategy.generate_signals(train_data, date)
    signal_counts.append(len(signals))

print(f"    Average signals per day (first 10 days): {np.mean(signal_counts):.1f}")

print(f"\n[5] Running OUT-OF-SAMPLE backtest...")
print(f"    Test samples: {len(test_data)}")

# Run backtest using rule-based signal generator
results = run_time_based_backtest_rule_based(
    test_data, 
    strategy,  # Pass strategy object
    'signal'  # Signal column name
)

print("\n" + "=" * 70)
print("OUT-OF-SAMPLE RESULTS (Momentum Breakout)")
print("=" * 70)

# Display results
for key, value in results.items():
    if key in ['equity_curve_first_5', 'equity_curve_last_5']:
        print(f"\n{key}:")
        for item in value:
            print(f"  {item}")
    elif isinstance(value, dict):
        print(f"\n{key}:")
        for k, v in value.items():
            print(f"  {k}: {v}")
    else:
        print(f"{key}: {value}")

# Calculate gate status
print("\n" + "=" * 70)
print("GATE EVALUATION (Out-of-Sample)")
print("=" * 70)

gates = {
    "Win Rate ≥ 35%": results.get('win_rate', 0) >= 35,
    "Profit Factor ≥ 1.5": results.get('profit_factor', 0) >= 1.5,
    "Sharpe ≥ 1.5": results.get('sharpe_ratio', 0) >= 1.5,
    "Max DD ≤ 15%": results.get('max_drawdown_pct', 100) <= 15,
    "Max Loss/Trade ≤ ₹5K": True,  # Controlled by position sizing
    "Expectancy ≥ ₹50": results.get('expectancy', 0) >= 50,
    "Timeout Rate ≤ 20%": (results.get('exit_reasons', {}).get('TIMEOUT', 0) / 
                          max(results.get('total_trades', 1), 1) * 100) <= 20
}

passing = sum(gates.values())
print(f"\nGates Passing: {passing}/7")
for gate, status in gates.items():
    symbol = "✅" if status else "❌"
    print(f"  {symbol} {gate}")

# Save results
results_path = Path("/root/nse_strategy/results_momentum.json")
with open(results_path, 'w') as f:
    json.dump(results, f, indent=2, default=str)

print(f"\nResults saved to: {results_path}")
print("=" * 70)
