#!/usr/bin/env python3
"""
Telegram Reporting Module for Paper Trading
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import urllib.request
import urllib.parse

from config import TELEGRAM_CHAT_ID, PORTFOLIO_SIZE, LOGS_DIR

logger = logging.getLogger(__name__)

# Get Telegram Bot Token from environment
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')


def send_telegram_message(message: str, chat_id: int = TELEGRAM_CHAT_ID) -> bool:
    """Send message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set, skipping Telegram notification")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'HTML'
    }
    
    try:
        data = urllib.parse.urlencode(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            if result.get('ok'):
                logger.info("Telegram message sent successfully")
                return True
            else:
                logger.error(f"Telegram API error: {result}")
                return False
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def format_currency(value: float) -> str:
    """Format currency value with commas."""
    return f"₹{value:,.2f}"


def get_intraday_progress():
    """Get intraday data accumulation progress."""
    intraday_dir = '/root/nse_strategy/data/lake'
    try:
        import glob
        csv_files = glob.glob(f"{intraday_dir}/*.parquet")
        # Each day's data is one CSV file with ~2,700 rows (per stock per day)
        # We need ~120 trading days
        days_collected = len(csv_files)
        return min(days_collected, 120)
    except:
        return 0


def send_daily_report(report_data: Dict):
    """Send formatted daily trading report per spec."""
    today = datetime.now().strftime('%Y-%m-%d')
    
    equity = report_data.get('equity', PORTFOLIO_SIZE)
    return_pct = report_data.get('metrics', {}).get('return_pct', 0)
    open_positions = report_data.get('open_positions', [])
    closed_today = report_data.get('closed_today', [])
    signals = report_data.get('signals', [])
    taken_signals = report_data.get('taken_signals', [])
    metrics = report_data.get('metrics', {})
    
    # Header
    message = f"""📊 <b>PAPER TRADING DAILY REPORT — {today}</b>

💼 <b>PORTFOLIO:</b> {format_currency(equity)} ({return_pct:+.2f}% since start)
"""
    
    # Open Positions with unrealized P&L
    message += f"\n📈 <b>OPEN POSITIONS ({len(open_positions)}):</b>\n"
    if open_positions:
        for pos in open_positions:
            pos_status = pos.get('status', 'OPEN')
            if pos_status == 'PENDING':
                message += f"⏳ {pos['symbol']} {pos['direction']} | Entry: {format_currency(pos.get('planned_entry_price', 0))} | SL: {format_currency(pos['sl_price'])} | <i>Pending</i>\n"
            else:
                entry_price = pos['entry_price']
                current_price = pos.get('current_price', entry_price)
                direction = pos['direction']
                shares = pos.get('shares', 0)
                
                if direction == 'LONG':
                    unrealized = (current_price - entry_price) * shares
                else:
                    unrealized = (entry_price - current_price) * shares
                
                rsi = pos.get('current_rsi', pos.get('rsi_at_entry', 0))
                bars = pos.get('bars_held', 0)
                pnl_emoji = "🟢" if unrealized > 0 else "🔴" if unrealized < 0 else "⚪"
                message += f"   {pnl_emoji} {pos['symbol']} {pos['direction']} | Entry: {format_currency(entry_price)} | Current: {format_currency(current_price)} | P&L: {format_currency(unrealized)} | Bars: {bars}/10 | RSI: {rsi:.1f}\n"
    else:
        message += "   <i>No open positions</i>\n"
    
    # Closed Today
    message += f"\n📉 <b>CLOSED TODAY ({len(closed_today)}):</b>\n"
    if closed_today:
        for trade in closed_today:
            pnl = trade.get('realized_pnl', 0)
            pnl_emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            message += f"   {pnl_emoji} {trade['symbol']} {trade['direction']} | P&L: {format_currency(pnl)} | Exit: {trade['exit_type']} | Bars held: {trade['bars_held']}\n"
    else:
        message += "   <i>No trades closed today</i>\n"
    
    # New Signals
    generated = len(signals)
    taken = len(taken_signals)
    skipped = generated - taken
    
    message += f"\n🆕 <b>NEW SIGNALS ({generated} scanned, {taken} taken, {skipped} skipped):</b>\n"
    if taken_signals:
        for sig in taken_signals:
            message += f"   ✅ {sig['symbol']} {sig['signal']} | Entry: {format_currency(sig['price'])} | SL: {format_currency(sig['sl_price'])} | RSI: {sig['rsi']:.1f}\n"
    
    skipped_signals = [s for s in signals if not s.get('taken', False)]
    if skipped_signals:
        for sig in skipped_signals[:3]:
            reason = sig.get('skip_reason', 'Unknown')
            message += f"   ⏭️ {sig['symbol']} {sig['signal']} | <i>{reason}</i>\n"
        if len(skipped_signals) > 3:
            message += f"   <i>...and {len(skipped_signals) - 3} more skipped</i>\n"
    
    if not signals:
        message += "   <i>No signals generated today</i>\n"
    
    # Running Metrics
    total_trades = metrics.get('total_trades', 0)
    win_rate = metrics.get('win_rate', 0)
    profit_factor = metrics.get('profit_factor', 0)
    expectancy = metrics.get('expectancy', 0)
    max_drawdown = metrics.get('max_drawdown', 0)
    
    message += f"""\n📊 <b>RUNNING METRICS (since start):</b>
   Trades: {total_trades} | Win Rate: {win_rate}% | PF: {profit_factor} | Expectancy: {format_currency(expectancy)}
   Drawdown: {max_drawdown}% | Return: {return_pct:+.2f}%
"""
    
    # Intraday progress
    intraday_days = get_intraday_progress()
    message += f"\n⏳ <b>INTRADAY DATA:</b> {intraday_days}/120 days accumulated"
    
    # Failed downloads warning
    failed = report_data.get('failed_symbols', [])
    if failed:
        message += f"\n\n⚠️ <i>Warning: Failed to download data for {len(failed)} symbols</i>"
    
    send_telegram_message(message)


def send_weekly_summary():
    """Send weekly trading summary with gate evaluation."""
    from config import TRADES_FILE, EQUITY_FILE, GATE_CRITERIA, PORTFOLIO_SIZE
    
    # Load data
    try:
        with open(TRADES_FILE, 'r') as f:
            trades = json.load(f)
        with open(EQUITY_FILE, 'r') as f:
            equity_data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load data for weekly summary: {e}")
        send_error_alert(f"Weekly summary failed: {e}")
        return
    
    today = datetime.now()
    week_ago = today - timedelta(days=7)
    week_ago_str = week_ago.strftime('%Y-%m-%d')
    
    # Filter this week's trades
    week_trades = [t for t in trades if t.get('exit_date', '') >= week_ago_str]
    current_equity = float(equity_data[-1]['equity']) if equity_data else PORTFOLIO_SIZE
    total_return = ((current_equity - PORTFOLIO_SIZE) / PORTFOLIO_SIZE) * 100
    
    # Calculate weekly stats
    week_pnl = sum(t['realized_pnl'] for t in week_trades) if week_trades else 0
    winning = len([t for t in week_trades if t['realized_pnl'] > 0])
    losing = len([t for t in week_trades if t['realized_pnl'] <= 0])
    total_closed = len(week_trades)
    
    # Calculate running metrics
    if trades:
        all_winning = [t for t in trades if t['realized_pnl'] > 0]
        all_losing = [t for t in trades if t['realized_pnl'] <= 0]
        win_rate = len(all_winning) / len(trades) * 100
        
        gross_profit = sum(t['realized_pnl'] for t in all_winning)
        gross_loss = abs(sum(t['realized_pnl'] for t in all_losing))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0
        expectancy = sum(t['realized_pnl'] for t in trades) / len(trades)
        
        # Calculate max drawdown
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
        win_rate = profit_factor = expectancy = max_dd = 0
    
    # Gate evaluation
    gate_status = []
    gate_status.append(("Win Rate ≥40%", win_rate >= GATE_CRITERIA['min_win_rate'], f"{win_rate:.1f}%"))
    gate_status.append(("Profit Factor ≥1.2", profit_factor >= GATE_CRITERIA['min_profit_factor'], f"{profit_factor:.2f}"))
    gate_status.append(("Expectancy ≥₹30", expectancy >= GATE_CRITERIA['min_expectancy'], f"₹{expectancy:.0f}"))
    gate_status.append(("Max Drawdown ≤15%", max_dd <= GATE_CRITERIA['max_drawdown_pct'], f"{max_dd:.1f}%"))
    
    gates_passed = sum(1 for _, passed, _ in gate_status if passed)
    
    message = f"""📊 <b>WEEKLY SUMMARY — Week of {week_ago_str[:10]}</b>

💰 <b>WEEK'S TRADES:</b>
   Closed: {total_closed} | Wins: {winning} | Losses: {losing}
   Weekly P&L: {format_currency(week_pnl)}

📈 <b>RUNNING METRICS (since start):</b>
   Total Trades: {len(trades)}
   Win Rate: {win_rate:.1f}%
   Profit Factor: {profit_factor:.2f}
   Expectancy: {format_currency(expectancy)}
   Max Drawdown: {max_dd:.1f}%
   Total Return: {total_return:+.2f}%

🎯 <b>GATE EVALUATION ({gates_passed}/5 passing):</b>
"""
    for gate_name, passed, value in gate_status:
        emoji = "✅" if passed else "❌"
        message += f"   {emoji} {gate_name} → {value}\n"
    
    # Intraday data progress
    intraday_days = get_intraday_progress()
    message += f"\n⏳ <b>INTRADAY DATA:</b> {intraday_days}/120 days accumulated"
    
    send_telegram_message(message)
    logger.info(f"Weekly summary sent ({gates_passed}/5 gates passing)")


def send_error_alert(error_message: str, is_error: bool = True):
    """Send error or alert notification."""
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    if is_error:
        emoji = "🚨"
        title = "ERROR ALERT"
    else:
        emoji = "ℹ️"
        title = "INFO"
    
    message = f"""{emoji} <b>PAPER TRADING {title}</b> — {today}

<pre>{error_message}</pre>
"""
    
    send_telegram_message(message)
    logger.info(f"Alert sent: {error_message}")


def send_position_alert(symbol: str, direction: str, action: str, price: float, pnl: float = 0):
    """Send individual position alert."""
    emoji = "🟢" if action == "OPEN" else "🔴"
    pnl_text = f" | P&L: {format_currency(pnl)}" if action == "CLOSE" and pnl != 0 else ""
    
    message = f"""{emoji} <b>POSITION {action}</b>

{symbol} {direction}
Price: {format_currency(price)}{pnl_text}
"""
    
    send_telegram_message(message)


def send_weekly_summary_command():
    """Entry point for weekly summary cron job."""
    send_weekly_summary()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "weekly":
        send_weekly_summary_command()
    elif len(sys.argv) > 1 and sys.argv[1] == "test":
        send_error_alert("Paper Trading bot is online and ready!", is_error=False)
    else:
        # Test daily report with sample data
        print("Usage: python telegram_report.py [weekly|test]")
