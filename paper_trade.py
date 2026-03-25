import json, logging, sys
from pathlib import Path
from datetime import datetime, date
import pandas as pd
import numpy as np
import joblib
import pandas_ta as ta
from dotenv import load_dotenv
load_dotenv("/root/nse_strategy/.env")

BASE_DIR = Path("/root/nse_strategy")
MODEL_DIR = BASE_DIR / "models" / "latest"
LAKE_DIR = BASE_DIR / "data" / "lake"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
today = date.today().isoformat()
paper_log = LOG_DIR / f"paper_trades_{today}.json"
run_log = LOG_DIR / f"paper_run_{today}.log"
# Note: paper_log is updated after parquet date is known

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(run_log), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("paper")

FEATURE_COLS = [
    "ret_1","ret_3","ret_5","log_ret_1","body",
    "upper_shadow","lower_shadow","hl_range","is_bullish",
    "ema5_dist","ema10_dist","ema20_dist","ema5_10_xo",
    "macd_hist","macd_bull","adx","di_diff",
    "rsi14","rsi_overbought","rsi_oversold",
    "stoch_k","stoch_d","stoch_bull","roc10","willr14",
    "atr_pct","bb_pct","bb_width","bb_squeeze",
    "vwap_dist","vol_ratio","high_vol","obv_trend",
    "mins_open","session","rolling_std5","rolling_std10",
    "price_rank20","prev_day_ret","day_num","day_of_week"
]

ATR_SL_MULT = 1.8
ATR_TP_MULT = 3.2
MIN_CONF = 0.55
CAPITAL = 100_000
BROKERAGE = 0.0003

def notify(title, message):
    import urllib.request, json, os
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    text = f"<b>{title}</b>\n{message}"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id" : chat_id,
        "text" : text,
        "parse_mode": "HTML"
    }).encode()
    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.warning(f"Telegram failed: {e}")

def load_model():
    with open(MODEL_DIR / "meta.json") as f:
        meta = json.load(f)
    best = meta["best_model"]
    fname = "rf_model.pkl" if best == "RandomForest" else "xgb_model.pkl"
    model = joblib.load(MODEL_DIR / fname)
    scaler = joblib.load(MODEL_DIR / "scaler.pkl")
    bt = meta["models"][best]["backtest"]
    log.info(f"Model: {best} | "
             f"WR={bt.get('win_rate_pct',0):.1f}% | "
             f"PF={bt.get('profit_factor',0):.3f} | "
             f"Sharpe={bt.get('sharpe_ratio',0):.3f}")
    return model, scaler, meta

def engineer(df):
    d = df.copy().sort_values("DateTime").reset_index(drop=True)
    d["ret_1"] = d["Close"].pct_change(1)
    d["ret_3"] = d["Close"].pct_change(3)
    d["ret_5"] = d["Close"].pct_change(5)
    d["log_ret_1"] = np.log(d["Close"] / d["Close"].shift(1))
    d["body"] = (d["Close"] - d["Open"]) / d["Open"]
    d["upper_shadow"] = (d["High"] - d[["Open","Close"]].max(axis=1)) / d["Open"]
    d["lower_shadow"] = (d[["Open","Close"]].min(axis=1) - d["Low"]) / d["Open"]
    d["hl_range"] = (d["High"] - d["Low"]) / d["Close"]
    d["is_bullish"] = (d["Close"] >= d["Open"]).astype(int)
    d["ema5"] = ta.ema(d["Close"], length=5)
    d["ema10"] = ta.ema(d["Close"], length=10)
    d["ema20"] = ta.ema(d["Close"], length=20)
    d["ema5_dist"] = (d["Close"] - d["ema5"]) / d["Close"]
    d["ema10_dist"] = (d["Close"] - d["ema10"]) / d["Close"]
    d["ema20_dist"] = (d["Close"] - d["ema20"]) / d["Close"]
    d["ema5_10_xo"] = (d["ema5"] > d["ema10"]).astype(int)
    macd = ta.macd(d["Close"], fast=12, slow=26, signal=9)
    d["macd_hist"] = macd.iloc[:,1].fillna(0) if macd is not None else 0.0
    d["macd_bull"] = (d["macd_hist"] > 0).astype(int)
    adx = ta.adx(d["High"], d["Low"], d["Close"], length=14)
    d["adx"] = adx.iloc[:,0] if adx is not None else 25.0
    d["di_diff"] = (adx.iloc[:,1] - adx.iloc[:,2]) if adx is not None else 0.0
    d["rsi14"] = ta.rsi(d["Close"], length=14)
    d["rsi_overbought"]= (d["rsi14"] > 70).astype(int)
    d["rsi_oversold"] = (d["rsi14"] < 30).astype(int)
    stoch = ta.stoch(d["High"], d["Low"], d["Close"], k=14, d=3)
    d["stoch_k"] = stoch.iloc[:,0] if stoch is not None else 50.0
    d["stoch_d"] = stoch.iloc[:,1] if stoch is not None else 50.0
    d["stoch_bull"] = (d["stoch_k"] > d["stoch_d"]).astype(int)
    d["roc10"] = ta.roc(d["Close"], length=10)
    d["willr14"] = ta.willr(d["High"], d["Low"], d["Close"], length=14)
    atr = ta.atr(d["High"], d["Low"], d["Close"], length=14)
    d["atr14"] = atr if atr is not None else d["Close"] * 0.005
    d["atr_pct"] = d["atr14"] / d["Close"]
    bb = ta.bbands(d["Close"], length=20, std=2)
    d["bb_pct"] = bb.iloc[:,4] if bb is not None else 0.5
    d["bb_width"] = ((bb.iloc[:,0]-bb.iloc[:,2])/bb.iloc[:,1]) if bb is not None else 0.02
    d["bb_squeeze"] = (d["bb_width"] < d["bb_width"].rolling(20).mean()).astype(int)
    cvp = (d["Close"] * d["Volume"]).cumsum()
    cv = d["Volume"].cumsum()
    d["vwap"] = cvp / cv.replace(0, np.nan)
    d["vwap_dist"] = (d["Close"] - d["vwap"]) / d["Close"]
    vm = d["Volume"].rolling(20).mean()
    d["vol_ratio"] = d["Volume"] / vm.replace(0, np.nan)
    d["high_vol"] = (d["vol_ratio"] > 1.5).astype(int)
    obv = ta.obv(d["Close"], d["Volume"])
    d["obv_trend"] = (obv > ta.ema(obv, length=10)).astype(int)
    d["min_of_day"] = d["DateTime"].dt.hour * 60 + d["DateTime"].dt.minute
    d["mins_open"] = d["min_of_day"] - (9 * 60 + 15)
    d["session"] = pd.cut(d["mins_open"], bins=[-1,30,120,270,999],
                          labels=[0,1,2,3]).astype(int)
    d["day_of_week"] = d["DateTime"].dt.dayofweek
    d["rolling_std5"] = d["ret_1"].rolling(5).std()
    d["rolling_std10"] = d["ret_1"].rolling(10).std()
    d["price_rank20"] = d["Close"].rolling(20).rank(pct=True)
    d["prev_day_ret"] = 0.0
    d["day_num"] = 0
    return d

def simulate_day(ticker_df, model, scaler):
    trades = []
    in_position = False
    entry = sl = target = qty = direction = None
    n = len(ticker_df)

    for i in range(25, n):
        bar = ticker_df.iloc[i]

        if in_position:
            if direction == "BUY":
                if bar["Low"] <= sl:
                    gross = (sl - entry) * qty
                    net = gross - (entry + sl) * qty * BROKERAGE
                    trades.append({
                        "action": direction, "entry": entry,
                        "exit": sl, "reason": "SL",
                        "net_pnl": round(net, 2),
                        "bar_time": str(bar["DateTime"])
                    })
                    in_position = False
                elif bar["High"] >= target:
                    gross = (target - entry) * qty
                    net = gross - (entry + target) * qty * BROKERAGE
                    trades.append({
                        "action": direction, "entry": entry,
                        "exit": target, "reason": "TARGET",
                        "net_pnl": round(net, 2),
                        "bar_time": str(bar["DateTime"])
                    })
                    in_position = False
            else:
                if bar["High"] >= sl:
                    gross = (entry - sl) * qty
                    net = gross - (entry + sl) * qty * BROKERAGE
                    trades.append({
                        "action": direction, "entry": entry,
                        "exit": sl, "reason": "SL",
                        "net_pnl": round(net, 2),
                        "bar_time": str(bar["DateTime"])
                    })
                    in_position = False
                elif bar["Low"] <= target:
                    gross = (entry - target) * qty
                    net = gross - (entry + target) * qty * BROKERAGE
                    trades.append({
                        "action": direction, "entry": entry,
                        "exit": target, "reason": "TARGET",
                        "net_pnl": round(net, 2),
                        "bar_time": str(bar["DateTime"])
                    })
                    in_position = False
            continue

        try:
            window = ticker_df.iloc[:i+1].copy()
            feat = engineer(window)
            row = feat.dropna(subset=FEATURE_COLS).iloc[-1]
        except Exception:
            continue

        X = pd.DataFrame([row[FEATURE_COLS]])
        Xs = scaler.transform(X)
        pred = model.predict(Xs)[0]
        prob = model.predict_proba(Xs)[0]
        conf = prob.max()

        if conf < MIN_CONF or pred == 1:
            continue

        ep = row["Close"]
        atr = row.get("atr14", ep * 0.005)
        act = "BUY" if pred == 2 else "SELL"
        sl_p = round(ep - atr*ATR_SL_MULT if act=="BUY" else ep + atr*ATR_SL_MULT, 2)
        tp_p = round(ep + atr*ATR_TP_MULT if act=="BUY" else ep - atr*ATR_TP_MULT, 2)
        q = int(CAPITAL / ep)
        rr = round(abs(tp_p-ep) / abs(sl_p-ep), 2) if sl_p != ep else 0

        if rr < 1.5 or q == 0:
            continue

        entry = ep; sl = sl_p; target = tp_p
        qty = q; direction = act
        in_position = True

    return trades

def main():
    log.info("=" * 55)
    log.info(f"NSE EOD PAPER BACKTEST -- {today}")
    log.info("=" * 55)

    parquets = sorted(LAKE_DIR.glob("*.parquet"))
    if not parquets:
        log.error("No data in lake. Run 01_ingest.py first.")
        notify("NSE Paper ERROR", "No data in lake")
        return

    latest_parquet = parquets[-1]
    test_date = latest_parquet.stem
    log.info(f"Running EOD backtest on: {test_date}")
    # Use test_date for log file naming to avoid midnight rollover issue
    paper_log = LOG_DIR / f"paper_trades_{test_date}.json"
    run_log_named = LOG_DIR / f"paper_run_{test_date}.log"

    model, scaler, meta = load_model()
    day_df = pd.read_parquet(latest_parquet)
    tickers = day_df["ticker"].unique()
    log.info(f"Tickers to score: {len(tickers)}")

    notify(
        "NSE EOD Backtest Started",
        f"Date: {test_date}\n"
        f"Tickers: {len(tickers)}\n"
        f"Model: {meta['best_model']}"
    )

    all_trades = []
    daily_pnl = 0.0

    for ticker in tickers:
        tdf = day_df[day_df["ticker"] == ticker].copy()
        if len(tdf) < 30:
            continue
        trades = simulate_day(tdf, model, scaler)
        for t in trades:
            t["ticker"] = ticker
            t["test_date"] = test_date
            daily_pnl += t["net_pnl"]
            all_trades.append(t)
            result = "WIN" if t["net_pnl"] > 0 else "LOSS"
            log.info(
                f"[{result}] {ticker:25s} {t['action']:4s} "
                f"Entry=Rs{t['entry']:.2f} "
                f"Exit=Rs{t['exit']:.2f} "
                f"{t['reason']:6s} "
                f"Net=Rs{t['net_pnl']:+.2f}"
            )

    wins = sum(1 for t in all_trades if t["net_pnl"] > 0)
    losses = len(all_trades) - wins
    wr = wins / len(all_trades) * 100 if all_trades else 0

    log.info("=" * 55)
    log.info(f"EOD RESULTS -- {test_date}")
    log.info(f"Total trades : {len(all_trades)}")
    log.info(f"Wins/Losses : {wins}/{losses}")
    log.info(f"Win Rate : {wr:.1f}%")
    log.info(f"Daily PnL : Rs{daily_pnl:+,.2f}")
    log.info("=" * 55)

    with open(paper_log, "w") as f:
        json.dump({
            "date" : today,
            "test_date" : test_date,
            "total_trades": len(all_trades),
            "wins" : wins,
            "losses" : losses,
            "win_rate" : round(wr, 2),
            "daily_pnl" : round(daily_pnl, 2),
            "trades" : all_trades
        }, f, indent=2)

    pnl_dir = "UP" if daily_pnl >= 0 else "DOWN"
    notify(
        f"EOD {pnl_dir} -- NSE Paper {test_date}",
        f"Trades: {len(all_trades)} | "
        f"Wins: {wins} | Losses: {losses}\n"
        f"WR: {wr:.1f}% | "
        f"PnL: Rs{daily_pnl:+,.0f}"
    )

if __name__ == "__main__":
    main()
