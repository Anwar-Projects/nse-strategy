"""
Full Health Check for NSE Trading System
Runs every Monday 8:00 AM IST
"""
import os
import sys
import json
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

IEOD_DIR = Path('/root/nse_strategy/data/lake')
LOG_DIR = Path('/root/nse_strategy/logs')
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN',
 '8793580045:AAHj3rtvjrkA112KUqzNkueRPCQb_sx0jkE')
CHAT_ID = '8541952881'

def send_telegram(msg):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    requests.post(url, data={'chat_id': CHAT_ID, 'text': msg, 'parse_mode': 'HTML'})

def check_ieod():
    files = sorted(IEOD_DIR.glob('*.parquet'))
    count = len(files)
    needed = 120
    pct = round(count/needed*100, 1)
    last_date = files[-1].stem if files else 'No files'
    
    # Calculate trading days to completion
    days_remaining = max(0, 120 - count)
    weeks = days_remaining / 5
    eta = (datetime.now() + timedelta(weeks=weeks)).strftime('%b %Y')
    
    return {'count': count, 'needed': needed, 'pct': pct,
            'last_date': last_date, 'days_remaining': days_remaining, 'eta': eta}

def check_signals():
    signal_log = LOG_DIR / 'combined_signals.log'
    if not signal_log.exists():
        return {'status': 'Never run', 'count': 0}
    lines = signal_log.read_text().strip().split('\n')
    return {'status': 'Running', 'count': len([l for l in lines if l.strip()])}

def check_cron():
    import subprocess
    result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    crons = result.stdout
    return {
        'IEOD workflow': 'GMAIL_APP_PASSWORD' in crons,
        'Paper trading': 'daily_run' in crons,
        'Signal engine': 'combined_signal' in crons,
        'Health check': 'health_check' in crons
    }

def run_health_check():
    ieod = check_ieod()
    signals = check_signals()
    crons = check_cron()
    
    report = f"""📊 NSE HEALTH CHECK - {datetime.now():%Y-%m-%d %H:%M}
━━━━━━━━━━━━━━━━━━━━━━━━━━━

📁 IEOD ACCUMULATION
 Days collected: {ieod['count']}/120 ({ieod['pct']}%)
 Last file date: {ieod['last_date']}
 Days remaining: {ieod['days_remaining']}
 ETA live signals: {ieod['eta']}

🎯 SIGNAL ENGINE
 Status: {signals['status']}
 Signals logged: {signals['count']}

⚙️ CRON JOBS
 IEOD workflow: {'✅' if crons['IEOD workflow'] else '❌'}
 Paper trading: {'✅' if crons['Paper trading'] else '❌'}
 Signal engine: {'✅' if crons['Signal engine'] else '❌'}
 Health check: {'✅' if crons['Health check'] else '❌'}

🚦 OVERALL STATUS
 {'✅ READY' if ieod['count'] >= 120 else '⏳ ACCUMULATING'}
 IEOD {ieod['pct']}% complete
"""
    
    print(report)
    send_telegram(report)
    return report

if __name__ == '__main__':
    run_health_check()
