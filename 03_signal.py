"""
=============================================================================
NSE INTRADAY STRATEGY — STEP 3: LIVE SIGNAL GENERATOR
=============================================================================
Loads the latest trained model and scores a fresh OHLCV bar in real-time.
This is the bridge between the ML model and the broker API (Step 4).

Usage (standalone test):
    python 03_signal.py --ticker RELINFRA.BE.NSE --date 2026-03-20

Usage (from broker integration):
    from signal_generator import SignalGenerator
    sg = SignalGenerator()
    signal = sg.score_bar(ticker, bar_df, prev_bars_df)
    # signal → {"action": "BUY"|"SELL"|"HOLD", "confidence": 0.72,
    #            "sl": 77.10, "target": 78.50, "qty": 1291}
=============================================================================
"""

import warnings
warnings.filterwarnings("ignore")

import json, argparse
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import pandas_ta as ta
import joblib

BASE_DIR   = Path(__file__).parent
MODEL_DIR  = BASE_DIR / "models" / "latest"


class SignalGenerator:
    """
    Loads saved model + scaler and produces trading signals for live bars.
    Thread-safe after __init__. Call score_bar() from your broker feed loop.
    """

    def __init__(self, model_dir: Path = MODEL_DIR):
        if not model_dir.exists():
            raise FileNotFoundError(
                f"No trained model found at {model_dir}. Run 02_train.py first."
            )
        meta_path = model_dir / "meta.json"
        with open(meta_path) as f:
            self.meta = json.load(f)

        # Load best model
        best = self.meta["best_model"]
        if best == "RandomForest":
            self.model = joblib.load(model_dir / "rf_model.pkl")
        else:
            self.model = joblib.load(model_dir / "xgb_model.pkl")

        self.scaler      = joblib.load(model_dir / "scaler.pkl")
        self.feature_cols= self.meta["feature_cols"]
        self.cfg         = self.meta["config"]
        self.best_model  = best

        print(f"[SignalGenerator] Loaded {best} model")
        print(f"  Trained on : {self.meta['train_dates']}")
        print(f"  Test date  : {self.meta['test_date']}")
        print(f"  F1 score   : {self.meta['models'][best]['f1']}")

    def _build_features(self,
                        ticker: str,
                        bars: pd.DataFrame,
                        prev_day_close: float = None) -> pd.DataFrame:
        """
        bars: DataFrame with columns [DateTime, Open, High, Low, Close, Volume]
              Must have at least 30 rows for indicators to be valid.
        """
        d = bars.copy().sort_values("DateTime").reset_index(drop=True)
        d["ticker"] = ticker

        d["ret_1"]        = d["Close"].pct_change(1)
        d["ret_3"]        = d["Close"].pct_change(3)
        d["ret_5"]        = d["Close"].pct_change(5)
        d["log_ret_1"]    = np.log(d["Close"] / d["Close"].shift(1))
        d["body"]         = (d["Close"] - d["Open"]) / d["Open"]
        d["upper_shadow"] = (d["High"] - d[["Open","Close"]].max(axis=1)) / d["Open"]
        d["lower_shadow"] = (d[["Open","Close"]].min(axis=1) - d["Low"])   / d["Open"]
        d["hl_range"]     = (d["High"] - d["Low"]) / d["Close"]
        d["is_bullish"]   = (d["Close"] >= d["Open"]).astype(int)

        d["ema5"]       = ta.ema(d["Close"], length=5)
        d["ema10"]      = ta.ema(d["Close"], length=10)
        d["ema20"]      = ta.ema(d["Close"], length=20)
        d["ema5_dist"]  = (d["Close"] - d["ema5"])  / d["Close"]
        d["ema10_dist"] = (d["Close"] - d["ema10"]) / d["Close"]
        d["ema20_dist"] = (d["Close"] - d["ema20"]) / d["Close"]
        d["ema5_10_xo"] = (d["ema5"] > d["ema10"]).astype(int)

        macd = ta.macd(d["Close"], fast=12, slow=26, signal=9)
        if macd is not None and not macd.empty:
            d["macd_hist"] = macd.iloc[:, 1].fillna(0)
            d["macd_bull"] = (d["macd_hist"] > 0).astype(int)
        else:
            d["macd_hist"] = 0.0; d["macd_bull"] = 0

        adx = ta.adx(d["High"], d["Low"], d["Close"], length=14)
        if adx is not None and not adx.empty:
            d["adx"]     = adx.iloc[:, 0]
            d["di_diff"] = adx.iloc[:, 1] - adx.iloc[:, 2]
        else:
            d["adx"] = 25.0; d["di_diff"] = 0.0

        d["rsi14"]          = ta.rsi(d["Close"], length=14)
        d["rsi_overbought"] = (d["rsi14"] > 70).astype(int)
        d["rsi_oversold"]   = (d["rsi14"] < 30).astype(int)

        stoch = ta.stoch(d["High"], d["Low"], d["Close"], k=14, d=3)
        if stoch is not None and not stoch.empty:
            d["stoch_k"]    = stoch.iloc[:, 0]
            d["stoch_d"]    = stoch.iloc[:, 1]
            d["stoch_bull"] = (d["stoch_k"] > d["stoch_d"]).astype(int)
        else:
            d["stoch_k"] = 50.0; d["stoch_d"] = 50.0; d["stoch_bull"] = 0

        d["roc10"]   = ta.roc(d["Close"], length=10)
        d["willr14"] = ta.willr(d["High"], d["Low"], d["Close"], length=14)

        atr = ta.atr(d["High"], d["Low"], d["Close"], length=14)
        d["atr14"]  = atr if atr is not None else d["Close"] * 0.005
        d["atr_pct"]= d["atr14"] / d["Close"]

        bb = ta.bbands(d["Close"], length=20, std=2)
        if bb is not None and not bb.empty:
            d["bb_upper"]   = bb.iloc[:, 0]
            d["bb_mid"]     = bb.iloc[:, 1]
            d["bb_lower"]   = bb.iloc[:, 2]
            d["bb_pct"]     = bb.iloc[:, 4]
            d["bb_width"]   = (d["bb_upper"] - d["bb_lower"]) / d["bb_mid"]
            d["bb_squeeze"] = (d["bb_width"] < d["bb_width"].rolling(20).mean()).astype(int)
        else:
            d["bb_pct"] = 0.5; d["bb_width"] = 0.02; d["bb_squeeze"] = 0

        d["cum_vol_price"] = (d["Close"] * d["Volume"]).cumsum()
        d["cum_vol"]       = d["Volume"].cumsum()
        d["vwap"]          = d["cum_vol_price"] / d["cum_vol"].replace(0, np.nan)
        d["vwap_dist"]     = (d["Close"] - d["vwap"]) / d["Close"]
        vol_ma             = d["Volume"].rolling(20).mean()
        d["vol_ratio"]     = d["Volume"] / vol_ma.replace(0, np.nan)
        d["high_vol"]      = (d["vol_ratio"] > 1.5).astype(int)
        obv                = ta.obv(d["Close"], d["Volume"])
        d["obv_ema"]       = ta.ema(obv, length=10)
        d["obv_trend"]     = (obv > d["obv_ema"]).astype(int)

        d["min_of_day"] = d["DateTime"].dt.hour * 60 + d["DateTime"].dt.minute
        d["mins_open"]  = d["min_of_day"] - (9 * 60 + 15)
        d["session"]    = pd.cut(
            d["mins_open"], bins=[-1, 30, 120, 270, 999], labels=[0, 1, 2, 3]
        ).astype(int)
        d["day_of_week"]= d["DateTime"].dt.dayofweek
        d["rolling_std5"]  = d["ret_1"].rolling(5).std()
        d["rolling_std10"] = d["ret_1"].rolling(10).std()
        d["price_rank20"]  = d["Close"].rolling(20).rank(pct=True)
        d["day_num"]    = 0

        if prev_day_close:
            d["prev_day_ret"] = (d["Open"].iloc[0] - prev_day_close) / prev_day_close
        else:
            d["prev_day_ret"] = 0.0

        return d

    def score_bar(self,
                  ticker: str,
                  bars: pd.DataFrame,
                  prev_day_close: float = None) -> dict:
        """
        Score the latest bar in `bars` and return a trading signal dict.

        Parameters
        ----------
        ticker          : e.g. "RELINFRA.BE.NSE"
        bars            : DataFrame [DateTime, Open, High, Low, Close, Volume]
                          should contain at least 30 bars (including current bar)
        prev_day_close  : previous trading day close price (for gap feature)

        Returns
        -------
        {
          "ticker"     : str,
          "bar_time"   : str,
          "action"     : "BUY" | "SELL" | "HOLD",
          "confidence" : float,
          "sl"         : float,
          "target"     : float,
          "atr"        : float,
          "qty"        : int,          # ₹1L notional
          "rr_ratio"   : float,
          "model"      : str,
          "raw_probs"  : {"DOWN": float, "FLAT": float, "UP": float}
        }
        """
        if len(bars) < 20:
            return {"action": "HOLD", "confidence": 0.0,
                    "reason": "Insufficient bars (need 20+)"}

        featured = self._build_features(ticker, bars, prev_day_close)
        row      = featured.iloc[-1]           # score only the latest bar

        # Check all features available
        missing = [c for c in self.feature_cols if pd.isna(row.get(c, np.nan))]
        if missing:
            return {"action": "HOLD", "confidence": 0.0,
                    "reason": f"NaN features: {missing[:5]}"}

        X    = pd.DataFrame([row[self.feature_cols]])
        X_s  = self.scaler.transform(X)
        pred = self.model.predict(X_s)[0]
        prob = self.model.predict_proba(X_s)[0]
        conf = prob.max()

        entry = row["Close"]
        atr   = row["atr14"] if not pd.isna(row.get("atr14", np.nan)) else entry * 0.005

        sl_dist = atr * self.cfg["atr_sl_mult"]
        tp_dist = atr * self.cfg["atr_tp_mult"]
        rr      = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0
        qty     = int(100_000 / entry)  # ₹1L notional

        if pred == 2 and conf >= self.cfg["min_conf"]:   # UP
            action = "BUY"
            sl     = round(entry - sl_dist, 2)
            target = round(entry + tp_dist, 2)
        elif pred == 0 and conf >= self.cfg["min_conf"]: # DOWN
            action = "SELL"
            sl     = round(entry + sl_dist, 2)
            target = round(entry - tp_dist, 2)
        else:
            action = "HOLD"
            sl     = round(entry - sl_dist, 2)
            target = round(entry + tp_dist, 2)

        return {
            "ticker"    : ticker,
            "bar_time"  : str(row["DateTime"]),
            "action"    : action,
            "confidence": round(conf, 4),
            "sl"        : sl,
            "target"    : target,
            "entry"     : round(entry, 2),
            "atr"       : round(atr, 4),
            "qty"       : qty,
            "rr_ratio"  : rr,
            "model"     : self.best_model,
            "raw_probs" : {
                "DOWN": round(float(prob[0]), 4),
                "FLAT": round(float(prob[1]), 4),
                "UP"  : round(float(prob[2]), 4),
            }
        }

    def batch_score(self,
                    ticker: str,
                    bars: pd.DataFrame,
                    prev_day_close: float = None) -> pd.DataFrame:
        """
        Score every bar in the DataFrame (for simulation / replay).
        Returns a DataFrame with signal columns appended.
        """
        featured = self._build_features(ticker, bars, prev_day_close)
        valid    = featured.dropna(subset=self.feature_cols)

        if valid.empty:
            return pd.DataFrame()

        X     = valid[self.feature_cols]
        X_s   = self.scaler.transform(X)
        preds = self.model.predict(X_s)
        probs = self.model.predict_proba(X_s)

        valid = valid.copy()
        valid["pred_label"] = preds
        valid["conf"]       = probs.max(axis=1)
        valid["prob_down"]  = probs[:, 0]
        valid["prob_flat"]  = probs[:, 1]
        valid["prob_up"]    = probs[:, 2]
        valid["action"]     = valid.apply(
            lambda r: "BUY"  if r["pred_label"] == 2 and r["conf"] >= self.cfg["min_conf"]
                      else "SELL" if r["pred_label"] == 0 and r["conf"] >= self.cfg["min_conf"]
                      else "HOLD",
            axis=1
        )
        return valid[["DateTime","Close","action","conf","prob_down","prob_flat","prob_up","atr14"]]


# ─── STANDALONE DEMO ────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NSE Signal Generator Demo")
    parser.add_argument("--ticker", default="RELINFRA.BE.NSE")
    parser.add_argument("--date",   default="2026-03-20")
    args = parser.parse_args()

    sg = SignalGenerator()

    # Load the day's data from lake for demo
    lake_file = BASE_DIR / "data" / "lake" / f"{args.date}.parquet"
    if not lake_file.exists():
        print(f"[ERROR] No lake data for {args.date}. Run 01_ingest.py first.")
        exit(1)

    day_df = pd.read_parquet(lake_file)
    ticker_df = day_df[day_df["ticker"] == args.ticker].copy()

    if ticker_df.empty:
        print(f"[ERROR] Ticker {args.ticker} not found in lake for {args.date}")
        print(f"Available: {day_df['ticker'].unique().tolist()}")
        exit(1)

    print(f"\n[Demo] Scoring {args.ticker} | {args.date}")
    print(f"  Bars available: {len(ticker_df)}")
    print(f"\n  Simulating bar-by-bar live scoring (last 10 bars):\n")

    BARS_NEEDED = 25
    for i in range(BARS_NEEDED, len(ticker_df)):
        window = ticker_df.iloc[:i+1].copy()
        sig = sg.score_bar(args.ticker, window)
        if sig["action"] != "HOLD":
            print(f"  *** {sig['action']} SIGNAL ***")
            print(f"      Time       : {sig['bar_time']}")
            print(f"      Entry      : ₹{sig['entry']}")
            print(f"      SL         : ₹{sig['sl']}")
            print(f"      Target     : ₹{sig['target']}")
            print(f"      Qty        : {sig['qty']} shares")
            print(f"      Confidence : {sig['confidence']:.1%}")
            print(f"      R:R Ratio  : 1:{sig['rr_ratio']}")
            print(f"      Probs      : ↓{sig['raw_probs']['DOWN']:.2f} "
                  f"→{sig['raw_probs']['FLAT']:.2f} "
                  f"↑{sig['raw_probs']['UP']:.2f}")
            print()
