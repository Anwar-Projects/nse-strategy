#!/usr/bin/env python3
"""
Momentum Breakout Strategy
20-day high breakout with volume confirmation and trend filters
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from datetime import datetime

class MomentumBreakoutStrategy:
    """
    20-Day High Breakout Strategy with ATR-based risk management
    """
    
    def __init__(self):
        self.name = "MomentumBreakout"
        self.lookback = 20  # days
        self.volume_multiplier = 1.5
        self.adx_threshold = 25
        self.rsi_min = 50
        self.rsi_max = 75
        self.min_avg_volume = 500_000
        self.max_holding_days = 15
        
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all technical indicators for each symbol"""
        
        df = df.copy().sort_values(['Symbol', 'Date']).reset_index(drop=True)
        unique_symbols = df['Symbol'].unique()
        
        all_data = []
        for symbol in unique_symbols:
            sym_df = df[df['Symbol'] == symbol].copy()
            
            if len(sym_df) < self.lookback:
                continue
            
            # 20-day high
            sym_df['high_20'] = sym_df['Close'].rolling(window=self.lookback).max()
            
            # Volume filter
            sym_df['volume_ma20'] = sym_df['Volume'].rolling(window=self.lookback).mean()
            sym_df['volume_filter'] = sym_df['Volume'] > (sym_df['volume_ma20'] * self.volume_multiplier)
            
            # Liquidity filter
            sym_df['avg_volume_20'] = sym_df['Volume'].rolling(window=self.lookback).mean()
            sym_df['is_liquid'] = sym_df['avg_volume_20'] >= self.min_avg_volume
            
            # ATR calculation
            tr1 = sym_df['High'] - sym_df['Low']
            tr2 = abs(sym_df['High'] - sym_df['Close'].shift(1))
            tr3 = abs(sym_df['Low'] - sym_df['Close'].shift(1))
            sym_df['tr'] = np.maximum(np.maximum(tr1, tr2), tr3)
            sym_df['atr14'] = sym_df['tr'].rolling(14).mean()
            
            # ADX calculation (simplified)
            sym_df['plus_dm'] = np.where(
                (sym_df['High'] - sym_df['High'].shift(1)) > (sym_df['Low'].shift(1) - sym_df['Low']),
                np.maximum(sym_df['High'] - sym_df['High'].shift(1), 0),
                0
            )
            sym_df['minus_dm'] = np.where(
                (sym_df['Low'].shift(1) - sym_df['Low']) > (sym_df['High'] - sym_df['High'].shift(1)),
                np.maximum(sym_df['Low'].shift(1) - sym_df['Low'], 0),
                0
            )
            
            # Smoothed directional movement
            sym_df['di_plus'] = 100 * sym_df['plus_dm'].rolling(14).mean() / sym_df['atr14']
            sym_df['di_minus'] = 100 * sym_df['minus_dm'].rolling(14).mean() / sym_df['atr14']
            
            dx = 100 * abs(sym_df['di_plus'] - sym_df['di_minus']) / (sym_df['di_plus'] + sym_df['di_minus'])
            sym_df['adx'] = dx.rolling(14).mean()
            
            # ADX filter
            sym_df['adx_filter'] = sym_df['adx'] >= self.adx_threshold
            
            # RSI calculation
            delta = sym_df['Close'].diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss
            sym_df['rsi14'] = 100 - (100 / (1 + rs))
            
            # RSI filter
            sym_df['rsi_filter'] = (sym_df['rsi14'] >= self.rsi_min) & (sym_df['rsi14'] <= self.rsi_max)
            
            # Breakout signal (price > 20-day high)
            sym_df['is_breakout'] = sym_df['Close'] > sym_df['high_20'].shift(1)
            
            # Combined signal
            sym_df['signal'] = (
                sym_df['is_breakout'] &
                sym_df['volume_filter'] &
                sym_df['adx_filter'] &
                sym_df['rsi_filter'] &
                sym_df['is_liquid']
            )
            
            # Signal strength (combination of RSI and ADX)
            sym_df['signal_strength'] = sym_df['adx'].fillna(0) + (sym_df['rsi14'].fillna(50) - 50)
            
            all_data.append(sym_df)
        
        return pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()
    
    def generate_signals(self, data: pd.DataFrame, current_date: datetime) -> List[Dict]:
        """Generate entry signals for the given date"""
        
        # Get signals for this date
        day_data = data[data['Date'] == current_date].copy()
        signals = []
        
        for _, row in day_data.iterrows():
            if row.get('signal', False) and pd.notna(row.get('atr14')):
                # Calculate entry at next day's open (use today's close as proxy for now)
                entry_price = row['Close']
                atr = row['atr14']
                
                if pd.isna(entry_price) or pd.isna(atr) or atr == 0:
                    continue
                
                sl_price = entry_price - (atr * 1.5)  # ATR_SL_MULT
                tp_price = entry_price + (atr * 4.5)  # ATR_TP_MULT
                
                signals.append({
                    'symbol': row['Symbol'],
                    'entry_price': entry_price,
                    'sl_price': sl_price,
                    'tp_price': tp_price,
                    'atr': atr,
                    'signal_strength': row.get('signal_strength', 50),
                    'direction': 'LONG',
                    'rsi': row.get('rsi14', 50),
                    'adx': row.get('adx', 25),
                    'volume_ratio': row.get('Volume', 0) / row.get('volume_ma20', 1)
                })
        
        # Sort by signal strength (RSI + ADX)
        signals.sort(key=lambda x: x['signal_strength'], reverse=True)
        
        return signals

if __name__ == "__main__":
    print("Momentum Breakout Strategy module loaded")
