#!/usr/bin/env python3
"""
Integration script: Load model, prepare data, run time-based backtest with 70/30 split
"""

import sys
import json
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
import pickle

# Add to path
sys.path.insert(0, '/root/nse_strategy')
from backtest_engine import run_time_based_backtest, Portfolio

print("=" * 70)
print("TIME-BASED BACKTEST INTEGRATION")
print("=" * 70)

# Load historical data
print("\n[1] Loading historical data...")
data_file = Path("/root/nse_strategy/data/historical/nifty50_daily_12m.csv")
df = pd.read_csv(data_file)

print(f"    Total rows: {len(df):,}")
print(f"    Date range: {df['Date'].min()} to {df['Date'].max()}")
print(f"    Symbols: {df['Symbol'].nunique()}")

# Prepare features
def build_features_daily(data):
    """Build features for daily data"""
    df = data.copy()
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values(['Symbol', 'Date']).reset_index(drop=True)
    
    # Group by symbol and calculate features
    dfs = []
    for symbol in df['Symbol'].unique():
        sym_df = df[df['Symbol'] == symbol].copy()
        
        # Price features
        sym_df['ret_1'] = sym_df['Close'].pct_change(1)
        sym_df['ret_3'] = sym_df['Close'].pct_change(3)
        sym_df['ret_5'] = sym_df['Close'].pct_change(5)
        sym_df['log_ret_1'] = np.log(sym_df['Close'] / sym_df['Close'].shift(1))
        
        # Candle features
        sym_df['body'] = sym_df['Close'] - sym_df['Open']
        sym_df['upper_shadow'] = sym_df['High'] - np.maximum(sym_df['Open'], sym_df['Close'])
        sym_df['lower_shadow'] = np.minimum(sym_df['Open'], sym_df['Close']) - sym_df['Low']
        sym_df['hl_range'] = sym_df['High'] - sym_df['Low']
        sym_df['is_bullish'] = (sym_df['Close'] > sym_df['Open']).astype(int)
        
        # Moving averages
        sym_df['ema5'] = sym_df['Close'].ewm(span=5).mean()
        sym_df['ema10'] = sym_df['Close'].ewm(span=10).mean()
        sym_df['ema20'] = sym_df['Close'].ewm(span=20).mean()
        sym_df['ema5_dist'] = (sym_df['Close'] - sym_df['ema5']) / sym_df['ema5']
        sym_df['ema10_dist'] = (sym_df['Close'] - sym_df['ema10']) / sym_df['ema10']
        sym_df['ema5_10_xo'] = ((sym_df['ema5'] > sym_df['ema10']).astype(int) - 
                                (sym_df['ema5'].shift(1) > sym_df['ema10'].shift(1)).astype(int)).abs()
        
        # Volatility (ATR using daily ranges)
        tr1 = sym_df['High'] - sym_df['Low']
        tr2 = abs(sym_df['High'] - sym_df['Close'].shift(1))
        tr3 = abs(sym_df['Low'] - sym_df['Close'].shift(1))
        sym_df['tr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        sym_df['atr14'] = sym_df['tr'].rolling(14).mean()
        sym_df['atr_pct'] = sym_df['atr14'] / sym_df['Close']
        
        # Technical indicators
        delta = sym_df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        sym_df['rsi14'] = 100 - (100 / (1 + rs))
        
        # VWAP (simplified for daily)
        sym_df['vwap'] = (sym_df['Close'] * sym_df['Volume']).cumsum() / sym_df['Volume'].cumsum()
        sym_df['vwap_dist'] = (sym_df['Close'] - sym_df['vwap']) / sym_df['vwap']
        
        # Label for training
        sym_df['label'] = sym_df['ret_1'].shift(-1).apply(lambda x: 
            2 if x > 0.003 else (0 if x < -0.003 else 1))
        
        dfs.append(sym_df)
    
    return pd.concat(dfs).dropna()

print("\n[2] Building features...")
df_features = build_features_daily(df)
print(f"    Feature rows: {len(df_features):,}")

# Feature columns for model
feature_cols = [
    'ret_1', 'ret_3', 'ret_5', 'log_ret_1',
    'body', 'upper_shadow', 'lower_shadow', 'hl_range', 'is_bullish',
    'ema5_dist', 'ema10_dist', 'ema5_10_xo', 'atr_pct', 'rsi14', 'vwap_dist'
]

# 70/30 split
all_dates = sorted(df_features['Date'].unique())
split_idx = int(len(all_dates) * 0.7)
train_dates = all_dates[:split_idx]
test_dates = all_dates[split_idx:]

print(f"\n[3] Train/Test Split:")
print(f"    Training: {train_dates[0]} to {train_dates[-1]} ({len(train_dates)} days - IN-SAMPLE)")
print(f"    Testing: {test_dates[0]} to {test_dates[-1]} ({len(test_dates)} days - OUT-OF-SAMPLE)")

# Prepare training data
train_data = df_features[df_features['Date'].isin(train_dates)]
X_train = train_data[feature_cols].values
y_train = train_data['label'].values

print(f"\n[4] Training model...")
print(f"    Train samples: {len(X_train):,}")

# Train RandomForest (simplified)
rf = RandomForestClassifier(
    n_estimators=100,
    max_depth=10,
    min_samples_leaf=50,
    random_state=42,
    n_jobs=-1
)
rf.fit(X_train, y_train)
print(f"    Model trained on {len(train_dates)} days")

# Save model for reuse
model_path = Path("/root/nse_strategy/models/rf_daily_model.pkl")
with open(model_path, 'wb') as f:
    pickle.dump(rf, f)

# Prepare test data
test_data = df_features[df_features['Date'].isin(test_dates)].copy()

print(f"\n[5] Running time-based backtest on test set...")
print(f"    Test samples: {len(test_data):,}")

# Run backtest
results = run_time_based_backtest(test_data, rf, feature_cols)

print("\n" + "=" * 70)
print("OUT-OF-SAMPLE RESULTS (Time-Based Backtest)")
print("=" * 70)

for key, value in results.items():
    if isinstance(value, dict):
        print(f"\n{key}:")
        for k, v in value.items():
            print(f"  {k}: {v}")
    elif isinstance(value, list) and len(value) > 0 and len(value) <= 10:
        print(f"\n{key}:")
        for item in value:
            print(f"  {item}")
    else:
        print(f"{key}: {value}")

# Save results
results_path = Path("/root/nse_strategy/results_time_based.json")
with open(results_path, 'w') as f:
    json.dump(results, f, indent=2, default=str)

print(f"\nResults saved to: {results_path}")
print("=" * 70)
