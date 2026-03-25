#!/usr/bin/env python3
"""
Mean Reversion Strategy - RSI-based with trend filters
Version A: Fixed TP at 3x ATR
Version B: Exit when RSI crosses back to neutral
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from datetime import datetime

class MeanReversionStrategy:
    """
    RSI Mean Reversion Strategy
    Buys oversold in uptrends, sells overbought in downtrends
    """
    
    def __init__(self, exit_version='A'):
        self.name = f"MeanReversion_V{exit_version}"
        self.exit_version = exit_version  # 'A' for fixed TP, 'B' for RSI exit
        
        # Filters
        self.rsi_period = 7
        self.rsi_oversold = 25  # Long entry
        self.rsi_overbought = 75  # Short entry
        self.rsi_neutral = 50  # Version B exit
        
        self.adx_threshold = 30  # Must be BELOW this (range-bound)
        self.sma_long_term = 200  # Trend filter
        self.sma_short_term = 10  # Pullback/rally filter
        self.min_avg_volume = 500_000
        
        # Risk management
        self.atr_sl_mult = 2.0  # Wider SL for mean reversion
        self.atr_tp_mult = 3.0  # 1:1.5 RR
        self.max_holding_bars = 10  # 2 weeks
        
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all indicators for mean reversion"""
        
        df = df.copy().sort_values(['Symbol', 'Date']).reset_index(drop=True)
        unique_symbols = df['Symbol'].unique()
        
        all_data = []
        for symbol in unique_symbols:
            sym_df = df[df['Symbol'] == symbol].copy()
            
            if len(sym_df) < self.sma_long_term:
                continue
            
            # RSI(7) - fast for mean reversion
            delta = sym_df['Close'].diff()
            gain = delta.clip(lower=0).rolling(self.rsi_period).mean()
            loss = (-delta.clip(upper=0)).rolling(self.rsi_period).mean()
            rs = gain / loss
            sym_df['rsi7'] = 100 - (100 / (1 + rs))
            
            # RSI crossed above/below 50 (for Version B)
            sym_df['rsi_above_50'] = sym_df['rsi7'] > self.rsi_neutral
            sym_df['rsi_cross_up_50'] = (~sym_df['rsi_above_50'].shift(1).fillna(False)) & sym_df['rsi_above_50']
            sym_df['rsi_cross_down_50'] = (sym_df['rsi_above_50'].shift(1).fillna(True)) & ~sym_df['rsi_above_50']
            
            # Long-term SMA (200-day)
            sym_df['sma200'] = sym_df['Close'].rolling(self.sma_long_term).mean()
            
            # Short-term SMA (10-day)
            sym_df['sma10'] = sym_df['Close'].rolling(self.sma_short_term).mean()
            
            # ADX for trend/range detection
            tr1 = sym_df['High'] - sym_df['Low']
            tr2 = abs(sym_df['High'] - sym_df['Close'].shift(1))
            tr3 = abs(sym_df['Low'] - sym_df['Close'].shift(1))
            sym_df['tr'] = np.maximum(np.maximum(tr1, tr2), tr3)
            sym_df['atr14'] = sym_df['tr'].rolling(14).mean()
            
            # Simplified ADX
            sym_df['plus_dm'] = np.where(
                (sym_df['High'] - sym_df['High'].shift(1)) > (sym_df['Low'].shift(1) - sym_df['Low']),
                np.maximum(sym_df['High'] - sym_df['High'].shift(1), 0), 0
            )
            sym_df['minus_dm'] = np.where(
                (sym_df['Low'].shift(1) - sym_df['Low']) > (sym_df['High'] - sym_df['High'].shift(1)),
                np.maximum(sym_df['Low'].shift(1) - sym_df['Low'], 0), 0
            )
            sym_df['adx'] = 100 * abs(sym_df['plus_dm'].rolling(14).mean() - sym_df['minus_dm'].rolling(14).mean()) / \
                          (sym_df['plus_dm'].rolling(14).mean() + sym_df['minus_dm'].rolling(14).mean() + 0.001)
            
            # ADX filter - must be BELOW threshold (range-bound)
            sym_df['adx_filter'] = sym_df['adx'] < self.adx_threshold
            
            # Liquidity filter
            sym_df['avg_volume'] = sym_df['Volume'].rolling(20).mean()
            sym_df['is_liquid'] = sym_df['avg_volume'] >= self.min_avg_volume
            
            # LONG entry: Oversold in uptrend
            sym_df['long_trend_ok'] = sym_df['Close'] > sym_df['sma200']  # Above 200-day SMA
            sym_df['long_pullback'] = sym_df['Close'] < sym_df['sma10']  # Below 10-day SMA (pullback)
            sym_df['long_rsi_ok'] = sym_df['rsi7'] < self.rsi_oversold  # RSI < 25
            
            sym_df['long_signal'] = (
                sym_df['long_trend_ok'] &
                sym_df['long_pullback'] &
                sym_df['long_rsi_ok'] &
                sym_df['adx_filter'] &
                sym_df['is_liquid']
            )
            
            # SHORT entry: Overbought in downtrend
            sym_df['short_trend_ok'] = sym_df['Close'] < sym_df['sma200']  # Below 200-day SMA
            sym_df['short_rally'] = sym_df['Close'] > sym_df['sma10']  # Above 10-day SMA (rally)
            sym_df['short_rsi_ok'] = sym_df['rsi7'] > self.rsi_overbought  # RSI > 75
            
            sym_df['short_signal'] = (
                sym_df['short_trend_ok'] &
                sym_df['short_rally'] &
                sym_df['short_rsi_ok'] &
                sym_df['adx_filter'] &
                sym_df['is_liquid']
            )
            
            # Signal strength
            sym_df['long_strength'] = (self.rsi_oversold - sym_df['rsi7']) + (30 - sym_df['adx'])
            sym_df['short_strength'] = (sym_df['rsi7'] - self.rsi_overbought) + (30 - sym_df['adx'])
            
            all_data.append(sym_df)
        
        return pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()
    
    def generate_signals(self, data: pd.DataFrame, current_date: datetime) -> Dict[str, List[Dict]]:
        """Generate both LONG and SHORT signals"""
        
        day_data = data[data['Date'] == current_date].copy()
        long_signals = []
        short_signals = []
        
        for _, row in day_data.iterrows():
            if not pd.notna(row.get('atr14')) or row['atr14'] == 0:
                continue
            
            # LONG signals
            if row.get('long_signal', False):
                entry_price = row['Close']
                atr = row['atr14']
                
                long_signals.append({
                    'symbol': row['Symbol'],
                    'entry_price': entry_price,
                    'sl_price': entry_price - (atr * self.atr_sl_mult),  # 2× ATR below
                    'tp_price': entry_price + (atr * self.atr_tp_mult),  # 3× ATR above
                    'atr': atr,
                    'signal_strength': row.get('long_strength', 10),
                    'direction': 'LONG',
                    'rsi': row.get('rsi7', 25),
                    'adx': row.get('adx', 25),
                    'version': self.exit_version
                })
            
            # SHORT signals
            if row.get('short_signal', False):
                entry_price = row['Close']
                atr = row['atr14']
                
                short_signals.append({
                    'symbol': row['Symbol'],
                    'entry_price': entry_price,
                    'sl_price': entry_price + (atr * self.atr_sl_mult),  # 2× ATR above (SL for shorts)
                    'tp_price': entry_price - (atr * self.atr_tp_mult),  # 3× ATR below (TP for shorts)
                    'atr': atr,
                    'signal_strength': row.get('short_strength', 10),
                    'direction': 'SHORT',
                    'rsi': row.get('rsi7', 75),
                    'adx': row.get('adx', 25),
                    'version': self.exit_version
                })
        
        long_signals.sort(key=lambda x: x['signal_strength'], reverse=True)
        short_signals.sort(key=lambda x: x['signal_strength'], reverse=True)
        
        return {'LONG': long_signals, 'SHORT': short_signals}
    
    def check_exit_vb(self, position: Dict, bar: pd.Series) -> Optional[str]:
        """Version B exit: Exit when RSI crosses back to neutral"""
        
        if position['direction'] == 'LONG':
            # Exit long when RSI crosses above 50
            if bar['rsi_cross_up_50']:
                return 'RSI_REVERSION'
        else:
            # Exit short when RSI crosses below 50
            if bar['rsi_cross_down_50']:
                return 'RSI_REVERSION'
        
        return None

if __name__ == "__main__":
    print("Mean Reversion Strategy module loaded")
