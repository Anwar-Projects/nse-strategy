# NSE Intraday ML Strategy Pipeline
**KAIZIFY — Anwar**

Production-grade machine learning pipeline for NSE intraday trading.
Trains on daily 1-minute OHLCV data, generates BUY/SELL signals with
ATR-based SL/Target, runs automated EOD paper backtesting, and sends
Telegram notifications daily.

---

## Architecture
Windows PC (192.168.10.5)
Data Feed ZIP via email
|
v
OpenClaw Agent (192.168.40.80)
Fetches email via GOG CLI
Extracts GFDLCM_STOCK_*.csv
|
v
CPU Server (192.168.40.100) GPU Server (192.168.40.90)
01_ingest.py —──────────────► gpu_train.sh
02_train.py ◄─────────────── CUDA XGBoost
03_signal.py RTX 2060 SUPER
04_broker.py
paper_trade.py
daily_report.py
|
v
Telegram Notifications (8PM IST)

---

## System Requirements

- Ubuntu 22.04+ or Debian 12+
- Python 3.10+
- 8GB+ RAM recommended
- NVIDIA GPU optional (tested RTX 2060 SUPER, 8GB)
- NSE 1-min OHLCV data feed (Global Data Feeds)
- Telegram bot for notifications

---

## Quick Start

### 1. Clone and set up
```bash
git clone https://github.com/YOUR_USERNAME/nse-strategy.git
cd nse-strategy
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
nano .env
# Add your Telegram credentials
```

### 3. Create required directories
```bash
mkdir -p data/lake incoming models/latest logs reports
```

### 4. Ingest first data file
```bash
# Supports both ZIP and raw CSV
python 01_ingest.py --zip /path/to/GFDLCM_STOCK_25032026.csv
python 01_ingest.py --status
```

### 5. Train the model
```bash
python 02_train.py
```

### 6. Run EOD paper backtest
```bash
python paper_trade.py
```

### 7. Set up automated cron
```bash
crontab -e

# Add these lines (adjust paths):
0 23 * * 1-5 /path/to/venv/bin/python3 /path/to/daily_run.sh >> logs/cron.log 2>&1
0 0 * * 1-5 /path/to/venv/bin/python3 /path/to/paper_trade.py >> logs/paper_run.log 2>&1
5 0 * * 1-5 /path/to/venv/bin/python3 /path/to/daily_report.py >> logs/report.log 2>&1
0 8 * * 1 /path/to/venv/bin/python3 /path/to/01_ingest.py --status >> logs/health.log 2>&1
```

---

## File Structure
```
nse-strategy/
|-- 01_ingest.py          # Data ingestion — CSV/ZIP to parquet lake
|-- 02_train.py           # ML training — Random Forest + XGBoost
|-- 03_signal.py          # Live signal generator
|-- 04_broker.py          # Broker API stub (Zerodha Kite)
|-- paper_trade.py        # EOD bar-by-bar backtester
|-- daily_run.sh          # Daily automation — ingest + retrain
|-- daily_report.py       # Telegram EOD results reporter
|-- backtest_engine.py    # Extended backtest engine
|-- mean_reversion_strategy.py    # Mean reversion strategy module
|-- momentum_breakout_strategy.py # Momentum breakout strategy module
|-- process_ieod_intraday.py    # IEOD intraday data processor
|-- requirements.txt      # Python dependencies
|-- .env.example          # Environment variables template
|-- .gitignore            # Git ignore rules
|-- README.md             # This file
|-- data/
|   `-- lake/             # Parquet files (one per trading day)
|-- models/
|   `-- latest/           # Trained model artifacts
|-- logs/                 # Daily run logs
|-- reports/              # Generated backtest charts
```

---

## Pipeline Components

### 01_ingest.py — Data Ingestion
Reads daily CSV data feed, filters for liquid equity stocks
(BE segment, 100+ candles, 50K+ volume, price >= Rs5),
saves as compressed parquet. Supports both ZIP and raw CSV.
Tracks all ingested dates in a registry.
```bash
python 01_ingest.py --zip GFDLCM_STOCK_25032026.csv
python 01_ingest.py --status
python 01_ingest.py --zip file.csv --force # re-ingest
```

### 02_train.py — ML Training
Trains Random Forest and XGBoost classifiers.
Selects best model by Sharpe Ratio from backtest
(not F1 — Sharpe is the correct trading metric).
Strict walk-forward: trains on all days except latest,
tests on latest day only. No future leakage.

### 03_signal.py — Live Signal Generator
Loads saved model and scores fresh bars in real-time.
Returns complete signal dict per bar:
BUY/SELL/HOLD, confidence, SL, Target, Qty, R:R ratio.
```bash
python 03_signal.py --ticker RELINFRA.BE.NSE --date 2026-03-25
```

### 04_broker.py — Broker Integration
Zerodha Kite Connect stub with paper trading mode.
Includes RiskManager (daily loss cap, max positions,
overtrading circuit breaker) and PaperBook ledger.
Swap KiteConnector class for other brokers.

### paper_trade.py — EOD Backtester
Bar-by-bar simulation on completed daily parquet data.
Checks SL/Target on each subsequent bar after entry.
Calculates net P&L after 0.03% brokerage per side.
Saves results to JSON log and sends Telegram summary.

### daily_run.sh — Daily Automation
1. Scans for new CSV files not yet ingested
2. Runs 01_ingest.py on each new file
3. Runs 02_train.py to retrain on all data
4. Evaluates 5 go-live gates
5. Saves metrics history

### daily_report.py — Telegram Reporter
Sends evening Telegram message with today's results,
last 5 days trend, model info, and data lake status.

---

## Features Engineered (41 total)

| Category | Features |
|---|---|
| Price action | ret_1/3/5, log_ret, body, shadows, hl_range, is_bullish |
| Trend | EMA 5/10/20 distance, EMA crossover, MACD hist, ADX, DI diff |
| Momentum | RSI(14), overbought/oversold flags, Stochastic K/D, ROC(10), Williams%R |
| Volatility | ATR(14)%, Bollinger %B, BB width, BB squeeze flag |
| Volume | VWAP deviation, volume ratio, high volume flag, OBV trend |
| Time | Minutes since open, session zone (0-3), day of week |
| Multi-day | Previous day return, day ordinal index |

---

## Strategy Parameters

| Parameter | Value | Description |
|---|---|---|
| FORWARD_BARS | 8 | Predict direction N bars ahead |
| UP_THRESHOLD | 0.3% | Min forward return to label UP |
| DOWN_THRESHOLD | -0.3% | Min forward return to label DOWN |
| MIN_CONF | 0.55 | Min model confidence to take trade |
| ATR_SL_MULT | 1.8x | Stop loss = entry +/- ATR x 1.8 |
| ATR_TP_MULT | 3.2x | Target = entry +/- ATR x 3.2 |
| R:R Ratio | 1:1.78 | Minimum reward-to-risk enforced |
| BROKERAGE | 0.03% | Per side (Zerodha intraday rate) |
| CAPITAL | Rs 1,00,000 | Notional per trade |

---

## Go-Live Gates

All five must pass consistently before live trading:

| Gate | Threshold | Current |
|---|---|---|
| Win Rate | >= 52% | 55.91% |
| Profit Factor | >= 1.3 | 3.394 |
| Sharpe Ratio | >= 1.5 | 8.218 |
| Max Drawdown | <= Rs 15,000 | Rs 4,497 |
| Targets > SL hits | Yes | Yes |

---

## Performance Results

Training data: 51 days (Jan 2026 to Mar 2026)

| Metric | Backtest | Live EOD Day 1 | Live EOD Day 2 |
|---|---|---|---|
| Win Rate | 55.91% | 66.2% | 74.4% |
| Profit Factor | 3.394 | — | — |
| Sharpe Ratio | 8.218 | — | — |
| Max Drawdown | Rs 4,497 | — | — |
| Daily PnL (simulated) | — | Rs +66,867 | Rs +79,538 |
| Best Model | XGBoost | XGBoost | XGBoost |

---

## Broker Integration

### Zerodha Kite Connect
```bash
pip install kiteconnect
export KITE_API_KEY=your_key
export KITE_ACCESS_TOKEN=your_token

python 04_broker.py --paper # paper mode — no real orders
python 04_broker.py --live # live mode — real orders
```

### Switching to Other Brokers
Replace KiteConnector class in 04_broker.py:

| Broker | SDK | Notes |
|---|---|---|
| Upstox | upstox-python | Similar API structure |
| Angel One | smartapi-python | REST based |
| IIFL | breeze-connect | WebSocket feed |
| Interactive Brokers | ibapi | For US/global markets |

---

## GPU Training Setup
```bash
# On GPU server (tested: RTX 2060 SUPER, 8GB VRAM)
mkdir -p /root/nse_gpu/{models,logs}
python3 -m venv /root/nse_gpu/venv
/root/nse_gpu/venv/bin/pip install xgboost pandas numpy scikit-learn pandas-ta joblib pyarrow

# Set up SSH key from GPU server to CPU server
ssh-keygen -t rsa -b 4096 -f /root/.ssh/nse_gpu_key -N ""
# Add public key to CPU server authorized_keys

# Copy gpu_train.sh and set CPU_SERVER IP
# Add cron on GPU server:
# 30 16 * * 1-5 /root/nse_gpu/gpu_train.sh >> /root/nse_gpu/logs/cron.log 2>&1
```

XGBoost uses CUDA automatically when GPU is available.
Training time: ~7 minutes on RTX 2060 SUPER vs ~20 min on CPU.

---

## Data Feed Setup

Data source: Global Data Feeds (globaldatafeeds.in)
Email: noreply@globaldatafeeds.in
Format: Outer ZIP containing GFDLCM_STOCK_DDMMYYYY.zip
```
globaldatafeedsieoddatacashsegmentfor24_03_2026.zip
|-- GFDLCM_STOCK_24032026.zip <-- extract this
|-- GFDLCM_INDICES_24032026.zip <-- ignore
```

Windows to Linux bulk transfer:
```bash
# On Windows — run nse_transfer.py
python nse_transfer.py
# Extracts all ZIPs and transfers CSVs to Linux incoming/
```

---

## Infrastructure Map
- 192.168.40.80 OpenClaw host — agent orchestration + email fetch
- 192.168.40.90 GPU server — CUDA XGBoost training (RTX 2060 SUPER)
- 192.168.40.100 CPU server — main pipeline, cron, paper trading
- 192.168.10.5 Windows PC — data feed source, broker apps

---

## Automated Daily Schedule
- ~16:00 IST Data feed email arrives from globaldatafeeds.in
- ~18:00 IST OpenClaw fetches CSV via GOG CLI, transfers to CPU server
- 23:00 IST daily_run.sh — ingest new CSV + retrain model
- 00:00 IST paper_trade.py — EOD bar-by-bar backtest
- 00:05 IST daily_report.py — Telegram summary to phone
- 08:00 IST 01_ingest.py --status — weekly health check (Monday)

---

## Risk Controls

- Max 3 concurrent positions per day
- Max 15 trades per day (circuit breaker)
- Daily loss cap: Rs 5,000 (auto-halt if hit)
- No new positions in last 15 min of session
- Min confidence filter: 55%
- Min R:R filter: 1.5
- No overnight carry (intraday MIS only)

---

## Author

**Anwar**
Associate Vice President — IT Audit, RAKBANK
Certifications: OSCP, CISA, CISSP
Company: KAIZIFY — Cybersecurity, Software Development, Corporate Training

---

## Disclaimer

This software is for educational and research purposes only.
Past backtest performance does not guarantee future results.
Always validate thoroughly with paper trading before risking capital.
The author accepts no responsibility for financial losses.
