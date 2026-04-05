#!/bin/bash
# Setup cron jobs for momentum strategy and intraday data

# Daily paper trading run for momentum strategy (4:30 PM IST = 11:00 UTC)
(crontab -l 2>/dev/null; echo "# Momentum Strategy Paper Trading"; echo "0 11 * * 1-5 /root/nse_strategy/venv/bin/python3 /root/nse_strategy/run_momentum_paper.py") | crontab -

# Daily intraday data processing (5:30 PM IST = 12:00 UTC)
(crontab -l 2>/dev/null; echo ""; echo "# Intraday Data Processing"; echo "30 12 * * 1-5 /root/nse_strategy/venv/bin/python3 /root/nse_strategy/process_ieod_intraday.py") | crontab -

# Weekly summary (Friday 6 PM IST = 13:00 UTC)
(crontab -l 2>/dev/null; echo ""; echo "# Weekly Summary Report"; echo "0 13 * * 5 /root/nse_strategy/venv/bin/python3 /root/nse_strategy/telegram_reporter.py --weekly") | crontab -

echo "Cron jobs configured:"
crontab -l | tail -15
