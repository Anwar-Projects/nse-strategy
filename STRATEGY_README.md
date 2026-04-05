# NSE Intraday ML Strategy Pipeline
**KAIZIFY — Anwar**  
1-Min Price Direction Classifier → ATR-based SL/Target → Live Trading Bridge

---

## Architecture

```
Daily Zip File
     │
     ▼
01_ingest.py          ← Run every day after receiving zip
     │  Parquet lake (data/lake/YYYY-MM-DD.parquet)
     ▼
02_train.py           ← Train on all past days, test on latest day
     │  Model artifacts (models/latest/)
     ▼
03_signal.py          ← Real-time bar scorer (call from broker loop)
     │  BUY/SELL/HOLD + SL + Target + Qty
     ▼
04_broker.py          ← Broker integration (paper/live)
                         Currently: Zerodha Kite stub
```

---

## Quick Start

### First-time setup
```bash
pip install pandas numpy scikit-learn xgboost pandas-ta joblib pyarrow matplotlib seaborn
```

### Day 1 — Ingest your first data file
```bash
python 01_ingest.py --zip /path/to/GFDLCM_STOCK_20032026.zip
python 01_ingest.py --status   # verify
```

### Train the model (bootstrap mode on day 1)
```bash
python 02_train.py
```

### Check status of data lake anytime
```bash
python 01_ingest.py --status
```

### Day 2 onwards — add new day, retrain
```bash
python 01_ingest.py --zip /path/to/GFDLCM_STOCK_21032026.zip
python 02_train.py
# Now trains on Day 1, tests on Day 2 — true out-of-sample
```

### Test signal generator
```bash
python 03_signal.py --ticker RELINFRA.BE.NSE --date 2026-03-20
```

### Paper trading loop (market hours only)
```bash
python 04_broker.py --paper
```

---

## Walk-Forward Logic

| Days in Lake | Training Data      | Test Data      |
|-------------:|--------------------|----------------|
| 1 day        | First 60% of day   | Last 40% (bootstrap) |
| 2 days       | Day 1              | Day 2          |
| 5 days       | Days 1–4           | Day 5          |
| 30 days      | Days 1–29          | Day 30         |

**Rule**: Model is NEVER trained on data it is tested on. No leakage.

---

## Strategy Parameters

| Parameter         | Value    | Description                        |
|-------------------|----------|------------------------------------|
| FORWARD_BARS      | 5        | Predict price direction in 5 bars  |
| UP_THRESHOLD      | +0.3%    | Min move to label as UP            |
| DOWN_THRESHOLD    | -0.3%    | Min move to label as DOWN          |
| MIN_CONF          | 0.55     | Min model confidence to trade      |
| ATR_SL_MULT       | 1.5×     | SL = entry ± ATR × 1.5            |
| ATR_TP_MULT       | 2.5×     | Target = entry ± ATR × 2.5        |
| R:R Ratio         | 1 : 1.67 | Minimum reward-to-risk             |
| BROKERAGE         | 0.03%    | Per side (Zerodha-like)            |
| CAPITAL           | ₹1,00,000| Per trade notional                 |

---

## Features Used (38 total)

**Price action**: ret_1/3/5, body, shadows, hl_range  
**Trend**: EMA 5/10/20 distance, MACD histogram, ADX/DI  
**Momentum**: RSI(14), Stochastic(14,3), ROC(10), Williams%R  
**Volatility**: ATR(14)%, Bollinger %B, BB width/squeeze  
**Volume**: VWAP deviation, volume ratio, OBV trend  
**Time**: minutes since open, session zone, day of week  
**Multi-day**: prev_day_ret (gap), day_num (ordinal)  

---

## Output Files

```
models/
  latest/
    rf_model.pkl        ← Random Forest (scikit-learn)
    xgb_model.pkl       ← XGBoost
    scaler.pkl          ← StandardScaler
    meta.json           ← config, dates, F1 scores

data/
  lake/
    2026-03-20.parquet  ← one file per trading day
  registry.json         ← tracks all ingested dates

reports/
  run_YYYYMMDD_HHMMSS/
    01_equity_curves.png
    02_feature_importance.png
    03_trade_analytics.png
    04_pnl_by_ticker.png
    trade_log_RandomForest.csv

logs/
  broker.log            ← live trading log
  eod_YYYY-MM-DD.json   ← end-of-day summary
```

---

## Broker Integration (Step 4)

Current stub supports **Zerodha Kite**. To activate:

```bash
pip install kiteconnect
export KITE_API_KEY=your_key
export KITE_ACCESS_TOKEN=your_token
python 04_broker.py --paper   # test first
python 04_broker.py --live    # real money
```

For other brokers — swap `KiteConnector` class:
- **Upstox**: use `upstox-python` SDK, same method signatures
- **Angel One**: use `smartapi-python`
- **IIFL**: use `breeze-connect`

---

## Risk Controls Built In

- Max 3 concurrent positions
- Max 15 trades per day (overtrading circuit breaker)  
- Daily loss cap: ₹5,000 (halt all trading if hit)
- No new positions in last 15 min of session (15:15+)
- Min confidence filter: 0.55
- Min R:R filter: 1.5
- No overnight carry (position checked at each bar)

---

## ⚠️ Important Disclaimers

1. **Single day of training data** is not enough for live trading. Accumulate 30+ days minimum before going live.
2. **BE segment stocks** (T2T) cannot be squared off intraday — delivery mandatory. Switch to EQ segment data for true intraday.
3. **Past backtest results do not guarantee future performance.**
4. Always start with paper trading. Validate for 2–4 weeks before risking capital.
