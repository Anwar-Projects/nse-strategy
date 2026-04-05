#!/bin/bash
# Agent startup script — run this on every agent restart

echo "========================================="
echo "🤖 AGENT STARTUP — AUTO-RESTORE CONTEXT"
echo "========================================="
echo ""
echo "Reading project context..."
cat /root/nse_strategy/AGENT_CONTEXT.md
echo ""
echo "========================================="
echo "SYSTEM STATUS:"
echo "========================================="
echo ""
echo "--- CRON JOBS ---"
crontab -l
echo ""
echo "--- OPEN POSITIONS ---"
cat /root/nse_strategy/paper_trading/positions.json
echo ""
echo "--- CURRENT EQUITY ---"
cat /root/nse_strategy/paper_trading/equity.json
echo ""
echo "--- LAST RUN LOG last 30 lines ---"
tail -30 /root/nse_strategy/paper_trading/logs/daily_run.log
echo ""
echo "========================================="
echo "✅ Context restored. Ready for instructions."
echo "========================================="
