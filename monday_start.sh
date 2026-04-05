#!/bin/bash
# Monday startup script for paper trading

echo "Starting NSE Paper Trading for Monday..."
echo "Current time: $(date)"
echo "Current IST: $(TZ=Asia/Kolkata date)"

# Kill any existing daily_run processes
echo "Cleaning up existing processes..."
pkill -f "daily_run.py" 2>/dev/null
sleep 2

# Start fresh
echo "Starting paper trading scheduler..."
cd /root/nse_strategy
/root/nse_strategy/venv/bin/python3 paper_trading/daily_run.py &
echo "Started with PID: $!"
echo ""
echo "Scheduler will run at:"
echo "  - 09:35 AM (server time) - Market open, fill pending positions"
echo "  - 15:35 PM (server time) - Market close, process exits"
echo ""
echo "To check status: tail -f /root/nse_strategy/paper_trading/logs/daily_run.log"
