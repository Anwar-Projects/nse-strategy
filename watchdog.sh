#!/bin/bash
# Watchdog — runs at 5:00 PM IST, checks if daily_run.py executed today

TODAY=$(date +%Y-%m-%d)
LAST_RUN=$(grep -c "$TODAY" /root/nse_strategy/paper_trading/logs/daily_run.log 2>/dev/null || echo 0)

if [ "$LAST_RUN" -eq 0 ]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id=8541952881 \
        -d text="⚠️ WATCHDOG ALERT - $(date)%0A%0ADaily paper trading did NOT execute today.%0ACheck system immediately."
    echo "$(date): ALERT SENT" >> /root/nse_strategy/logs/watchdog.log
else
    echo "$(date): OK - Daily run executed" >> /root/nse_strategy/logs/watchdog.log
fi
