#!/usr/bin/env python3
"""
Paper Trading Health Check Module
Runs Monday 8:00 AM IST to verify system health
"""

import os
import json
import glob
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple
from telegram_report import send_telegram_message, format_currency

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# File paths
PAPER_TRADING_DIR = Path('/root/nse_strategy/paper_trading')
DATA_DIR = Path('/root/nse_strategy/data')
INTRADAY_DIR = DATA_DIR / 'intraday'
CONFIG_FILE = PAPER_TRADING_DIR / 'config.py'


def check_json_valid(filepath: Path) -> Tuple[bool, str]:
    """Check if a JSON file is valid and readable."""
    try:
        with open(filepath, 'r') as f:
            json.load(f)
        return True, "Valid JSON"
    except FileNotFoundError:
        return False, "File not found"
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"
    except Exception as e:
        return False, f"Error: {e}"


def check_daily_run_execution() -> Tuple[bool, str]:
    """Check if daily_run.py was executed every trading day last week."""
    logs_dir = PAPER_TRADING_DIR / 'logs'
    today = datetime.now()
    
    # Check last 7 days
    missing_days = []
    executed_days = []
    
    for i in range(7, 0, -1):
        check_date = today - timedelta(days=i)
        if check_date.weekday() >= 5:  # Skip weekends
            continue
        
        date_str = check_date.strftime('%Y-%m-%d')
        log_file = logs_dir / f'daily_run_{date_str}.log'
        
        if log_file.exists():
            # Check if log contains "Daily run completed"
            content = log_file.read_text()
            if "Daily run completed" in content or "Starting Daily Paper Trading Run" in content:
                executed_days.append(date_str)
            else:
                missing_days.append(f"{date_str} (incomplete)")
        else:
            missing_days.append(date_str)
    
    if missing_days:
        return False, f"Missing runs: {', '.join(missing_days)} | Executed: {len(executed_days)} days"
    return True, f"All {len(executed_days)} trading days executed"


def check_equity_curve_updates() -> Tuple[bool, str]:
    """Check if equity curve is being updated."""
    equity_file = PAPER_TRADING_DIR / 'equity.json'
    
    status, msg = check_json_valid(equity_file)
    if not status:
        return False, msg
    
    try:
        with open(equity_file, 'r') as f:
            equity_data = json.load(f)
        
        if not equity_data:
            return False, "Empty equity file"
        
        # Check last update
        last_entry = equity_data[-1]
        last_date = datetime.strptime(last_entry['date'], '%Y-%m-%d')
        today = datetime.now()
        days_since = (today - last_date).days
        
        if days_since > 3:  # Allow weekends
            return False, f"Stale data (last update: {days_since} days ago)"
        
        current_equity = float(last_entry['equity'])
        return True, f"Current equity: {format_currency(current_equity)} (updated {days_since} day(s) ago)"
    except Exception as e:
        return False, f"Error checking equity: {e}"


def check_intraday_accumulation() -> Tuple[bool, str]:
    """Check intraday data accumulation progress."""
    try:
        csv_files = list(INTRADAY_DIR.glob('*.csv')) if INTRADAY_DIR.exists() else []
        days_collected = len(csv_files)
        
        if days_collected == 0:
            return False, "No intraday data collected"
        elif days_collected < 30:
            return True, f"Early stage: {days_collected}/120 days ({(days_collected/120)*100:.1f}%)"
        elif days_collected < 60:
            return True, f"Building: {days_collected}/120 days ({(days_collected/120)*100:.1f}%)"
        elif days_collected < 90:
            return True, f"Good progress: {days_collected}/120 days ({(days_collected/120)*100:.1f}%)"
        else:
            return True, f"Near completion: {days_collected}/120 days ({(days_collected/120)*100:.1f}%)"
    except Exception as e:
        return False, f"Error checking intraday data: {e}"


def check_positions_file() -> Tuple[bool, str]:
    """Check if positions.json is valid."""
    positions_file = PAPER_TRADING_DIR / 'positions.json'
    status, msg = check_json_valid(positions_file)
    
    if status:
        try:
            with open(positions_file, 'r') as f:
                positions = json.load(f)
            open_count = len([p for p in positions if p.get('status') == 'OPEN'])
            pending_count = len([p for p in positions if p.get('status') == 'PENDING'])
            return True, f"Valid JSON | Open: {open_count} | Pending: {pending_count}"
        except Exception as e:
            return False, f"Parse error: {e}"
    return status, msg


def check_trades_file() -> Tuple[bool, str]:
    """Check if trades.json is valid."""
    trades_file = PAPER_TRADING_DIR / 'trades.json'
    status, msg = check_json_valid(trades_file)
    
    if status:
        try:
            with open(trades_file, 'r') as f:
                trades = json.load(f)
            return True, f"Valid JSON | Total trades: {len(trades)}"
        except Exception as e:
            return False, f"Parse error: {e}"
    return status, msg


def check_files_exist() -> List[Tuple[str, bool, str]]:
    """Check critical files exist."""
    results = []
    
    files_to_check = [
        ('daily_run.py', PAPER_TRADING_DIR / 'daily_run.py'),
        ('config.py', PAPER_TRADING_DIR / 'config.py'),
        ('telegram_report.py', PAPER_TRADING_DIR / 'telegram_report.py'),
        ('health_check.py', PAPER_TRADING_DIR / 'health_check.py'),
    ]
    
    for name, filepath in files_to_check:
        if filepath.exists():
            results.append((name, True, "File exists"))
        else:
            results.append((name, False, "MISSING"))
    
    return results


def run_health_check():
    """Run full health check and send report."""
    today = datetime.now().strftime('%Y-%m-%d')
    report_lines = []
    all_pass = True
    
    # Header
    report_lines.append(f"🩺 <b>PAPER TRADING HEALTH CHECK — {today}</b>\n")
    
    # File existence check
    report_lines.append("📁 <b>CRITICAL FILES:</b>")
    file_checks = check_files_exist()
    for name, exists, msg in file_checks:
        emoji = "✅" if exists else "❌"
        if not exists:
            all_pass = False
        report_lines.append(f"   {emoji} {name}: {msg}")
    
    # JSON validity checks
    report_lines.append("\n💾 <b>STATE FILES:</b>")
    
    pos_ok, pos_msg = check_positions_file()
    trades_ok, trades_msg = check_trades_file()
    equity_ok, equity_msg = check_equity_curve_updates()
    
    report_lines.append(f"   {'✅' if pos_ok else '❌'} positions.json: {pos_msg}")
    report_lines.append(f"   {'✅' if trades_ok else '❌'} trades.json: {trades_msg}")
    report_lines.append(f"   {'✅' if equity_ok else '❌'} equity.json: {equity_msg}")
    
    if not (pos_ok and trades_ok and equity_ok):
        all_pass = False
    
    # Daily run execution
    report_lines.append("\n⏰ <b>DAILY RUN EXECUTION:</b>")
    run_ok, run_msg = check_daily_run_execution()
    report_lines.append(f"   {'✅' if run_ok else '⚠️'} {run_msg}")
    if not run_ok:
        all_pass = False  # Warning, not critical
    
    # Intraday accumulation
    report_lines.append("\n📈 <b>INTRADAY DATA ACCUMULATION:</b>")
    intra_ok, intra_msg = check_intraday_accumulation()
    report_lines.append(f"   {'✅' if intra_ok else '❌'} {intra_msg}")
    if not intra_ok:
        all_pass = False
    
    # Summary
    report_lines.append(f"\n{'✅' if all_pass else '⚠️'} <b>OVERALL STATUS: {'HEALTHY' if all_pass else 'NEEDS ATTENTION'}</b>")
    
    # Send report
    message = "\n".join(report_lines)
    send_telegram_message(message)
    logger.info(f"Health check completed: {'HEALTHY' if all_pass else 'NEEDS ATTENTION'}")


if __name__ == "__main__":
    run_health_check()
