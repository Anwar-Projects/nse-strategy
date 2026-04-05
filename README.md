# NSE Intraday Strategy — Bollinger Band Mean Reversion

Automated paper trading system for NSE Nifty50 stocks using
Bollinger Band mean reversion on 3-minute intraday bars.

## Strategy Overview

| Metric | Value |
|--------|-------|
| Universe | Nifty 50 stocks |
| Timeframe | 3-minute bars |
| Strategy | Bollinger Band Mean Reversion |
| Risk per trade | ₹2,000 |
| Max simultaneous trades | 5 |
| Session | 9:15 AM → 3:10 PM IST |

## Backtested Performance (Jan-Apr 2026, 46 days)

| Metric | Result |
|--------|--------|
| Total trades | 368 |
| Win rate | 50.8% |
| Profit factor | 4.76x |
| Total P&L | +₹3,17,705 |
| Avg daily P&L | +₹6,906 |
| Best day | +₹38,424 |
| Worst day | -₹13,309 |

## Signal Logic (3-min Bollinger Bands)

### BUY Signal (all conditions must be true):
- Price closes below/at lower BB band
- RSI(14) < 40 (oversold)
- Previous bar confirmed below lower band
- Current bar closes back ABOVE lower band (reversal)
- Volume >= 1.5x 20-bar average
- Stock above 20-day daily EMA (bullish trend)

### SELL Signal (all conditions must be true):
- Price closes above/at upper BB band
- RSI(14) > 60 (overbought)
- Previous bar confirmed above upper band
- Current bar closes back BELOW upper band (reversal)
- Volume >= 1.5x 20-bar average
- Stock below 20-day daily EMA (bearish trend)

## Trade Management

| Stage | Action |
|-------|--------|
| Entry | Market order at bar close |
| SL | Min(4×ATR, 10% from 10-day EMA, 10% from 15-min swing) |
| Breakeven | Move SL to entry when 25% of TP1 covered |
| Partial exit | Close 50% at TP1 (middle BB band) |
| Trail | 15% gap trailing stop on remaining 50% |
| TP2 | Opposite BB band |
| Hard exit | 3:10 PM IST (no overnight positions) |

## Market Regime Filter

| Regime | Rule |
|--------|------|
| BULL (Nifty > 10-day EMA) | BUY signals only |
| BEAR (Nifty < 10-day EMA) | SELL signals only |
| CHOPPY (within 0.5% of EMA) | Both directions |

## Project Structure

```
nse_strategy/
├── nse_combined_strategy.py ← Main strategy (BB mean reversion)
├── paper_trading/
│   ├── daily_run.py ← EOD paper trading scheduler
│   ├── combined_signal_engine.py ← yfinance + IEOD signal combiner
│   ├── health_check_full.py ← Weekly health check + Telegram
│   ├── process_ieod_intraday.py ← IEOD data processor
│   └── config.py ← Configuration
├── 01_ingest.py ← NSE IEOD data ingestion
├── gmail_fetch_ieod.py ← Gmail IEOD attachment fetcher
├── data/
│   ├── lake/ ← Daily parquet files (gitignored)
│   └── registry.json ← Ingestion registry
├── requirements.txt
└── README.md
```

## Setup

```bash
# 1. Clone repo
git clone https://github.com/Anwar-Projects/nse-strategy.git
cd nse-strategy

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure Gmail for IEOD data
export GMAIL_APP_PASSWORD=your_16_char_app_password

# 5. Run backtest
python3 nse_combined_strategy.py --mode backtest --date 2026-04-02

# 6. Run paper trading
python3 nse_combined_strategy.py --mode paper
```

## Data Pipeline

```
Global Datafeeds email (annu19@gmail.com)
 → gmail_fetch_ieod.py (fetches daily ZIP)
 → 01_ingest.py (extracts NSE EQ stocks → parquet)
 → data/lake/YYYY-MM-DD.parquet
 → nse_combined_strategy.py (reads parquet → 3-min bars → signals)
```

## Cron Schedule (ollama-dell 192.168.40.100)

| Job | Time (IST) | Purpose |
|-----|-----------|---------|
| daily_run.py | 9:35 AM | Paper trading signals |
| run_ieod_with_auth.sh | 5:30 PM | Fetch + process IEOD |
| combined_signal_engine.py | 9:00 AM | Combined signal check |
| health_check_full.py | Monday 8 AM | Weekly health report |

## Infrastructure

| Machine | IP | Role |
|---------|-----|------|
| ollama-dell | 192.168.40.100 | NSE strategy server |
| ollama-cpu | 192.168.40.110 | GP FX training |
| Windows PC | 192.168.10.5 | MT5 paper trading |

## Disclaimer

This is a paper trading system for research purposes only.
Not financial advice. Past backtest performance does not
guarantee future results. Always consult a SEBI registered
advisor before investing real capital.
