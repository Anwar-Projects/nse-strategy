#!/usr/bin/env python3
"""
Time-Based Backtest Engine with Rule-Based Strategy Support
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import List, Dict, Optional
import json

# Configuration
MAX_OPEN_TRADES = 2
MAX_EXPOSURE_PCT = 0.50
PORTFOLIO_SIZE = 100_000
MAX_RISK_PER_TRADE = 5_000
ATR_SL_MULT = 1.5
ATR_TP_MULT = 4.5
FORWARD_BARS = 15  # Changed to 15 for swing trading
MAX_TRADE_PNL_PCT = 0.15

class Portfolio:
    """Portfolio tracking with real capital constraints"""
    
    def __init__(self, starting_capital: float = PORTFOLIO_SIZE):
        self.starting_capital = starting_capital
        self.current_equity = starting_capital
        self.available_capital = starting_capital
        self.total_exposure = 0.0
        self.peak_equity = starting_capital
        self.open_positions: List[Dict] = []
        self.closed_positions: List[Dict] = []
        self.equity_curve: List[Dict] = []
        self.skipped_signals: List[Dict] = []
        self.trade_count = 0
        self.max_concurrent = 0
        self.max_exposure = 0.0
        
    def record_equity(self, date: datetime):
        current_open = len(self.open_positions)
        self.max_concurrent = max(self.max_concurrent, current_open)
        self.max_exposure = max(self.max_exposure, self.total_exposure)
        
        self.equity_curve.append({
            'date': date.strftime('%Y-%m-%d'),
            'equity': round(self.current_equity, 2),
            'available_capital': round(self.available_capital, 2),
            'total_exposure': round(self.total_exposure, 2),
            'open_positions': current_open
        })
        self.peak_equity = max(self.peak_equity, self.current_equity)
    
    def calculate_position_size(self, entry_price: float, sl_price: float) -> int:
        sl_amount = abs(entry_price - sl_price)
        if sl_amount == 0:
            return 0
        
        max_qty_risk = int(MAX_RISK_PER_TRADE / sl_amount)
        max_qty_capital = int(self.available_capital / entry_price) if entry_price > 0 else 0
        remaining_exposure = (PORTFOLIO_SIZE * MAX_EXPOSURE_PCT) - self.total_exposure
        max_qty_exposure = int(remaining_exposure / entry_price) if remaining_exposure > 0 else 0
        
        return min(max_qty_risk, max_qty_capital, max_qty_exposure)
    
    def open_position(self, symbol: str, date: datetime, entry_price: float,
                     qty: int, sl_price: float, tp_price: float,
                     direction: str, confidence: float = 0.5) -> Optional[Dict]:
        
        assert len(self.open_positions) <= MAX_OPEN_TRADES, \
            f"MAX_OPEN_TRADES violated"
        
        margin = qty * entry_price
        assert self.total_exposure + margin <= (PORTFOLIO_SIZE * MAX_EXPOSURE_PCT) + 100, \
            f"MAX_EXPOSURE violated"
        
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
            'highest_price': entry_price,
            'trailing_stage': 0,
            'margin_required': margin,
            'confidence': confidence,
            'bars_held': 0,
            'status': 'OPEN'
        }
        
        self.open_positions.append(position)
        self.trade_count += 1
        self.available_capital -= margin
        self.total_exposure += margin
        
        return position
    
    def close_position(self, position: Dict, exit_price: float, 
                      exit_date: datetime, reason: str):
        
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
    
    def update_trailing_stop(self, position: Dict, bar: pd.Series):
        if position['direction'] != 'LONG':
            return
        
        entry = position['entry_price']
        sl_amount = abs(entry - position['sl_price'])
        highest = max(position['highest_price'], bar['High'])
        position['highest_price'] = highest
        current_price = bar['Close']
        
        # Stage 1: Breakeven
        if current_price >= entry + (sl_amount * 1.0) and position['current_sl'] <= entry:
            position['current_sl'] = entry * 1.001
            position['trailing_stage'] = 1
        # Stage 2: Trail at 1x SL below highest
        elif current_price >= entry + (sl_amount * 2.0):
            new_sl = highest - sl_amount
            if new_sl > position['current_sl']:
                position['current_sl'] = new_sl
                position['trailing_stage'] = 2
        # Stage 3: Tighten to 0.75x SL
        elif current_price >= entry + (sl_amount * 2.5):
            new_sl = highest - (sl_amount * 0.75)
            if new_sl > position['current_sl']:
                position['current_sl'] = new_sl
                position['trailing_stage'] = 3
    
    def skip_signal(self, symbol: str, date: datetime, reason: str):
        self.skipped_signals.append({
            'symbol': symbol,
            'date': date.strftime('%Y-%m-%d'),
            'reason': reason
        })


def run_time_based_backtest_rule_based(test_data: pd.DataFrame, signal_generator,
                                      signal_column: str = 'close_above_20dhigh') -> Dict:
    """Run time-based backtest with rule-based signal generator"""
    
    portfolio = Portfolio()
    all_dates = sorted(test_data['Date'].unique())
    
    print(f"\nRunning momentum breakout backtest...")
    print(f"Test period: {len(all_dates)} days")
    
    signals_considered = 0
    signals_filtered = 0  # No filtering for rule-based
    signals_taken = 0
    
    for current_date in all_dates:
        current_dt = pd.to_datetime(current_date)
        todays_data = test_data[test_data['Date'] == current_date].copy()
        
        if len(todays_data) == 0:
            continue
        
        # STEP 1: Check open positions
        for position in portfolio.open_positions.copy():
            symbol = position['symbol']
            symbol_bar = todays_data[todays_data['Symbol'] == symbol]
            
            if len(symbol_bar) == 0:
                position['bars_held'] += 1
                continue
            
            bar = symbol_bar.iloc[0]
            position['bars_held'] += 1
            
            portfolio.update_trailing_stop(position, bar)
            
            # Check exits for LONGS
            if position['direction'] == 'LONG':
                if bar['Low'] <= position['current_sl']:
                    portfolio.close_position(position, position['current_sl'], current_dt, 'SL')
                    continue
                if bar['High'] >= position['tp_price']:
                    portfolio.close_position(position, position['tp_price'], current_dt, 'TARGET')
                    continue
            
            # Timeout
            if position['bars_held'] >= FORWARD_BARS:
                portfolio.close_position(position, bar['Close'], current_dt, 'TIMEOUT')
                continue
        
        # STEP 2: Generate signals using rule-based strategy
        signals = []
        for _, row in todays_data.iterrows():
            if row.get(signal_column, False):
                if pd.notna(row.get('atr14')):
                    entry_price = row['Close']
                    atr = row['atr14']
                    
                    if pd.isna(entry_price) or pd.isna(atr) or atr == 0:
                        continue
                    
                    sl_price = entry_price - (atr * ATR_SL_MULT)
                    tp_price = entry_price + (atr * ATR_TP_MULT)
                    
                    signals.append({
                        'symbol': row['Symbol'],
                        'entry_price': entry_price,
                        'sl_price': sl_price,
                        'tp_price': tp_price,
                        'atr': atr,
                        'direction': 'LONG',
                        'signal_strength': row.get('adx', 50) + (row.get('rsi14', 50) - 50)
                    })
        
        signals.sort(key=lambda s: s['signal_strength'], reverse=True)
        signals_considered += len(signals)
        
        # STEP 3: Open positions
        for signal in signals:
            symbol = signal['symbol']
            
            holding_symbols = [p['symbol'] for p in portfolio.open_positions]
            if symbol in holding_symbols:
                portfolio.skip_signal(symbol, current_dt, 'already_holding')
                continue
            
            if len(portfolio.open_positions) >= MAX_OPEN_TRADES:
                portfolio.skip_signal(symbol, current_dt, 'max_open_trades')
                break
            
            entry_price = signal['entry_price']
            sl_price = signal['sl_price']
            tp_price = signal['tp_price']
            
            qty = portfolio.calculate_position_size(entry_price, sl_price)
            
            if qty == 0:
                portfolio.skip_signal(symbol, current_dt, 'position_size_zero')
                continue
            
            trade_cost = qty * entry_price
            
            if portfolio.total_exposure + trade_cost > PORTFOLIO_SIZE * MAX_EXPOSURE_PCT:
                portfolio.skip_signal(symbol, current_dt, 'max_exposure')
                continue
            
            if trade_cost > portfolio.available_capital:
                portfolio.skip_signal(symbol, current_dt, 'insufficient_capital')
                continue
            
            portfolio.open_position(symbol, current_dt, entry_price, qty,
                                  sl_price, tp_price, signal['direction'], signal['signal_strength'])
            signals_taken += 1
        
        portfolio.record_equity(current_dt)
    
    # Close remaining
    last_date = pd.to_datetime(all_dates[-1])
    last_data = test_data[test_data['Date'] == all_dates[-1]]
    
    for position in portfolio.open_positions.copy():
        symbol = position['symbol']
        symbol_bar = last_data[last_data['Symbol'] == symbol]
        exit_price = symbol_bar.iloc[0]['Close'] if len(symbol_bar) > 0 else position['entry_price']
        portfolio.close_position(position, exit_price, last_date, 'end_of_backtest')
    
    return calculate_metrics(portfolio, signals_considered, signals_filtered, signals_taken)


def calculate_metrics(portfolio: Portfolio, sig_considered: int, sig_filtered: int, sig_taken: int) -> Dict:
    """Calculate backtest metrics"""
    
    closed = portfolio.closed_positions
    if len(closed) == 0:
        return {'error': 'No trades executed', 'total_trades': 0}
    
    # Exit reasons
    exit_reasons = {}
    for p in closed:
        reason = p.get('exit_reason', 'unknown')
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    
    # P&L
    wins = [p for p in closed if p['net_pnl'] > 0]
    losses = [p for p in closed if p['net_pnl'] <= 0]
    
    total_profit = sum(p['net_pnl'] for p in wins)
    total_loss = abs(sum(p['net_pnl'] for p in losses))
    
    win_rate = len(wins) / len(closed) * 100 if len(closed) > 0 else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
    
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
    
    # Profit capture ratio
    tp_exits = [p for p in closed if p['exit_reason'] == 'TARGET']
    trailing_exits = [p for p in closed if 'TRAILING' in p.get('exit_reason', '')]
    
    capture_ratio = 0
    if tp_exits and trailing_exits:
        avg_tp = np.mean([p['net_pnl'] for p in tp_exits])
        avg_trailing = np.mean([p['net_pnl'] for p in trailing_exits])
        capture_ratio = avg_trailing / avg_tp if avg_tp > 0 else 0
    
    return {
        'total_days': len(portfolio.equity_curve),
        'total_trades': len(closed),
        'signals_considered': sig_considered,
        'signals_filtered': sig_filtered,
        'signals_taken': sig_taken,
        'signals_skipped': len(portfolio.skipped_signals),
        'win_rate': round(win_rate, 2),
        'profit_factor': round(profit_factor, 3),
        'sharpe_ratio': round(sharpe, 3),
        'max_drawdown': round(max_dd, 2),
        'max_drawdown_pct': round(max_dd_pct, 2),
        'expectancy': round(expectancy, 2),
        'total_profit': round(total_profit, 2),
        'total_loss': round(total_loss, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'exit_reasons': exit_reasons,
        'starting_equity': portfolio.starting_capital,
        'final_equity': round(portfolio.current_equity, 2),
        'portfolio_return': round(portfolio.current_equity - portfolio.starting_capital, 2),
        'return_pct': round(((portfolio.current_equity / portfolio.starting_capital) - 1) * 100, 2),
        'max_concurrent_observed': portfolio.max_concurrent,
        'max_exposure_observed': round(portfolio.max_exposure, 2),
        'profit_capture_ratio': round(capture_ratio, 3),
        'equity_curve_first_5': portfolio.equity_curve[:5],
        'equity_curve_last_5': portfolio.equity_curve[-5:]
    }


if __name__ == "__main__":
    print("Backtest Engine (Rule-Based) loaded.")
