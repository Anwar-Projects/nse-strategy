"""
=============================================================================
NSE INTRADAY STRATEGY — STEP 4: BROKER API INTEGRATION (STUB)
=============================================================================
Skeleton for live trading integration. Currently wired for Zerodha Kite.
Swap the KiteConnect calls for your broker's SDK (Upstox, Angel, IIFL etc.)

Architecture:
  ┌──────────────────┐    1-min bars     ┌──────────────────┐
  │  Broker WebSocket│ ─────────────────▶│ SignalGenerator  │
  │  (Kite Ticker)   │                   │ (03_signal.py)   │
  └──────────────────┘                   └────────┬─────────┘
                                                   │ BUY/SELL/HOLD
                                         ┌─────────▼─────────┐
                                         │  RiskManager      │
                                         │  (position limits,│
                                         │   daily loss cap) │
                                         └─────────┬─────────┘
                                                   │ approved order
                                         ┌─────────▼─────────┐
                                         │  OrderExecutor    │
                                         │  (Kite place_order│
                                         │   + GTT for SL/TP)│
                                         └───────────────────┘

To activate:
    pip install kiteconnect
    Set KITE_API_KEY and KITE_ACCESS_TOKEN in .env
    python 04_broker.py --paper       # paper trading mode
    python 04_broker.py --live        # REAL MONEY — be careful
=============================================================================
"""

import os, json, time, logging
from pathlib import Path
from datetime import datetime, date
from typing import Optional
import pandas as pd

# ─── LOGGING ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/broker.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("broker")
Path("logs").mkdir(exist_ok=True)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PAPER_MODE         = True          # ALWAYS start paper — flip via CLI arg
MAX_POSITIONS      = 3             # max concurrent open positions
MAX_CAPITAL_PER_TRADE = 100_000    # ₹1L per trade
DAILY_LOSS_CAP     = -5_000        # halt trading if daily P&L < -₹5,000
MAX_TRADES_PER_DAY = 15            # circuit breaker on overtrading

# Tickers to watch (from your liquid filter — update daily)
WATCH_LIST = [
    "RELINFRA", "EMBDL", "BCG", "AXITA",
    "HARDWYN", "KESORAMIND", "ZEEMEDIA", "TVSSCS",
]

# NSE exchange string for Kite
EXCHANGE = "NSE"

# ─── PAPER TRADE BOOK ────────────────────────────────────────────────────────
class PaperBook:
    """Simple in-memory paper trading ledger."""
    def __init__(self):
        self.positions  = {}   # ticker → {entry, sl, target, qty, direction}
        self.trades     = []
        self.daily_pnl  = 0.0
        self.trade_count= 0

    def open_position(self, ticker, direction, entry, sl, target, qty):
        if ticker in self.positions:
            log.warning(f"[PAPER] Already in position for {ticker}")
            return False
        self.positions[ticker] = {
            "direction": direction, "entry": entry, "sl": sl,
            "target": target, "qty": qty, "opened_at": datetime.now()
        }
        log.info(f"[PAPER] OPEN {direction} {ticker}  entry=₹{entry}  "
                 f"sl=₹{sl}  target=₹{target}  qty={qty}")
        return True

    def check_exits(self, ticker, high, low, current):
        """Call on each new bar to check if SL/Target hit."""
        if ticker not in self.positions:
            return None
        pos = self.positions[ticker]
        exit_reason = None

        if pos["direction"] == "LONG":
            if low  <= pos["sl"]:     exit_reason = "SL";     exit_px = pos["sl"]
            elif high >= pos["target"]: exit_reason = "TARGET"; exit_px = pos["target"]
        else:
            if high >= pos["sl"]:     exit_reason = "SL";     exit_px = pos["sl"]
            elif low  <= pos["target"]: exit_reason = "TARGET"; exit_px = pos["target"]

        if exit_reason:
            self._close(ticker, exit_px, exit_reason)
        return exit_reason

    def _close(self, ticker, exit_px, reason):
        pos    = self.positions.pop(ticker)
        mult   = 1 if pos["direction"] == "LONG" else -1
        gross  = (exit_px - pos["entry"]) * pos["qty"] * mult
        brok   = (pos["entry"] + exit_px) * pos["qty"] * 0.0003
        net    = gross - brok
        self.daily_pnl  += net
        self.trade_count += 1
        self.trades.append({
            "ticker": ticker, "direction": pos["direction"],
            "entry": pos["entry"], "exit": exit_px,
            "qty": pos["qty"], "reason": reason,
            "net_pnl": round(net, 2), "time": datetime.now()
        })
        log.info(f"[PAPER] CLOSE {ticker}  reason={reason}  "
                 f"exit=₹{exit_px}  net=₹{net:+.2f}  "
                 f"daily_pnl=₹{self.daily_pnl:+.2f}")

    def summary(self):
        return {
            "open_positions": len(self.positions),
            "trades_today"  : self.trade_count,
            "daily_pnl"     : round(self.daily_pnl, 2),
            "trade_log"     : self.trades,
        }


# ─── RISK MANAGER ────────────────────────────────────────────────────────────
class RiskManager:
    def __init__(self, book: PaperBook):
        self.book = book

    def approve(self, ticker: str, signal: dict) -> tuple[bool, str]:
        """Returns (approved, reason)."""
        s = self.book.summary()

        if s["daily_pnl"] <= DAILY_LOSS_CAP:
            return False, f"Daily loss cap hit (₹{s['daily_pnl']:,.0f})"

        if s["trades_today"] >= MAX_TRADES_PER_DAY:
            return False, f"Max trades/day reached ({MAX_TRADES_PER_DAY})"

        if s["open_positions"] >= MAX_POSITIONS:
            return False, f"Max concurrent positions ({MAX_POSITIONS})"

        if ticker in self.book.positions:
            return False, "Already in position"

        if signal["confidence"] < 0.55:
            return False, f"Low confidence ({signal['confidence']:.2f})"

        if signal["rr_ratio"] < 1.5:
            return False, f"Poor R:R ({signal['rr_ratio']})"

        # Session filter: avoid last 15 minutes (15:15+)
        now = datetime.now()
        if now.hour == 15 and now.minute >= 15:
            return False, "No new trades in last 15 min of session"

        return True, "OK"


# ─── KITE CONNECTOR (swap this class for your broker) ────────────────────────
class KiteConnector:
    """
    Wraps KiteConnect SDK. In paper mode all order calls are logged only.
    In live mode — real orders are placed. Handle with care.
    """

    def __init__(self, paper: bool = True):
        self.paper = paper
        self.kite  = None

        if not paper:
            try:
                from kiteconnect import KiteConnect
                api_key      = os.environ["KITE_API_KEY"]
                access_token = os.environ["KITE_ACCESS_TOKEN"]
                self.kite    = KiteConnect(api_key=api_key)
                self.kite.set_access_token(access_token)
                log.info("[Kite] Connected to live broker")
            except ImportError:
                raise ImportError("Run: pip install kiteconnect")
            except KeyError as e:
                raise EnvironmentError(f"Missing env var: {e}")

    def place_order(self, ticker, transaction_type, qty,
                    order_type="MARKET", price=None) -> Optional[str]:
        """Returns order_id or None."""
        if self.paper:
            oid = f"PAPER-{datetime.now().strftime('%H%M%S%f')}"
            log.info(f"[PAPER ORDER] {transaction_type} {qty}x{ticker}  "
                     f"type={order_type}  price={price}  id={oid}")
            return oid

        # ── LIVE ──────────────────────────────────────────────────────────────
        params = dict(
            tradingsymbol   = ticker.split(".")[0],
            exchange        = EXCHANGE,
            transaction_type= transaction_type,   # "BUY" or "SELL"
            quantity        = qty,
            order_type      = order_type,          # "MARKET" or "LIMIT"
            product         = "MIS",               # intraday
            validity        = "DAY",
        )
        if price:
            params["price"] = price
        order_id = self.kite.place_order(variety="regular", **params)
        log.info(f"[LIVE ORDER] {transaction_type} {qty}x{ticker}  id={order_id}")
        return order_id

    def place_gtt_sl_target(self, ticker, qty, sl_price, target_price,
                             direction="LONG") -> Optional[str]:
        """
        Places a Good-Till-Triggered bracket order for SL + Target.
        In paper mode: logs only.
        """
        if self.paper:
            log.info(f"[PAPER GTT] {ticker}  SL=₹{sl_price}  Target=₹{target_price}")
            return f"PAPER-GTT-{datetime.now().strftime('%H%M%S')}"

        exit_type = "SELL" if direction == "LONG" else "BUY"
        # Kite GTT (two-leg OCO)
        gtt = self.kite.place_gtt(
            trigger_type    = self.kite.GTT_TYPE_OCO,
            tradingsymbol   = ticker.split(".")[0],
            exchange        = EXCHANGE,
            trigger_values  = [sl_price, target_price],
            last_price      = self.kite.quote(f"{EXCHANGE}:{ticker.split('.')[0]}")
                              [f"{EXCHANGE}:{ticker.split('.')[0]}"]["last_price"],
            orders          = [
                {"transaction_type": exit_type, "quantity": qty,
                 "order_type": "LIMIT", "product": "MIS", "price": sl_price},
                {"transaction_type": exit_type, "quantity": qty,
                 "order_type": "LIMIT", "product": "MIS", "price": target_price},
            ]
        )
        return gtt

    def get_historical_1min(self, ticker, from_date, to_date) -> pd.DataFrame:
        """
        Fetch 1-minute OHLCV from Kite for the given ticker and date range.
        Returns DataFrame with [DateTime, Open, High, Low, Close, Volume].
        """
        if self.paper:
            log.info(f"[PAPER] get_historical — returning empty df")
            return pd.DataFrame()

        instrument_token = self._get_token(ticker)
        data = self.kite.historical_data(
            instrument_token, from_date, to_date, "minute"
        )
        df = pd.DataFrame(data)
        df.rename(columns={"date": "DateTime", "open": "Open", "high": "High",
                            "low": "Low", "close": "Close", "volume": "Volume"}, inplace=True)
        return df[["DateTime","Open","High","Low","Close","Volume"]]

    def _get_token(self, ticker: str) -> int:
        """Lookup instrument token from Kite instrument list."""
        instruments = self.kite.instruments(EXCHANGE)
        symbol      = ticker.split(".")[0]
        match       = [i for i in instruments if i["tradingsymbol"] == symbol]
        if not match:
            raise ValueError(f"Instrument not found: {symbol}")
        return match[0]["instrument_token"]


# ─── LIVE TRADING LOOP ───────────────────────────────────────────────────────
def run_trading_loop(paper: bool = True):
    """
    Main event loop. In production this would be driven by
    Kite WebSocket ticker feed (on_ticks callback).
    Here we simulate a bar-by-bar polling loop.
    """
    from strategy.signal_generator import SignalGenerator   # rename as needed

    log.info(f"{'='*60}")
    log.info(f"  LIVE TRADING LOOP  |  Mode: {'PAPER' if paper else '⚠️  LIVE'}")
    log.info(f"{'='*60}")

    sg      = SignalGenerator()
    kite    = KiteConnector(paper=paper)
    book    = PaperBook()
    risk    = RiskManager(book)

    today   = date.today().isoformat()
    bar_cache = {t: pd.DataFrame() for t in WATCH_LIST}

    # ── Intraday loop (runs every minute) ─────────────────────────────────────
    while True:
        now = datetime.now()

        # Only trade during market hours 09:15 – 15:25
        if not (9 * 60 + 15 <= now.hour * 60 + now.minute <= 15 * 60 + 25):
            if now.hour >= 15 and now.minute >= 30:
                log.info("Market closed. Shutting down.")
                break
            time.sleep(30)
            continue

        for ticker_sym in WATCH_LIST:
            ticker_full = f"{ticker_sym}.BE.NSE"

            # ── Fetch latest bar ──────────────────────────────────────────────
            new_bars = kite.get_historical_1min(
                ticker_full,
                from_date=f"{today} 09:15:00",
                to_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )

            if new_bars.empty:
                # In paper/demo mode, skip
                continue

            bar_cache[ticker_sym] = new_bars

            # ── Check exits for open positions ────────────────────────────────
            if ticker_full in book.positions:
                latest = new_bars.iloc[-1]
                reason = book.check_exits(
                    ticker_full,
                    high=latest["High"], low=latest["Low"],
                    current=latest["Close"]
                )
                if reason:
                    log.info(f"Position closed: {ticker_full}  ({reason})")
                continue    # don't signal if already in position

            # ── Score latest bar ─────────────────────────────────────────────
            signal = sg.score_bar(ticker_full, new_bars)
            if signal["action"] == "HOLD":
                continue

            # ── Risk check ────────────────────────────────────────────────────
            approved, reason = risk.approve(ticker_full, signal)
            if not approved:
                log.info(f"[RISK BLOCK] {ticker_full}  {reason}")
                continue

            # ── Execute ───────────────────────────────────────────────────────
            direction = signal["action"]   # "BUY" or "SELL"
            qty       = signal["qty"]
            entry     = signal["entry"]
            sl        = signal["sl"]
            target    = signal["target"]

            order_id = kite.place_order(ticker_full, direction, qty)
            if order_id:
                kite.place_gtt_sl_target(
                    ticker_full, qty, sl, target,
                    direction="LONG" if direction == "BUY" else "SHORT"
                )
                book.open_position(
                    ticker_full, direction, entry, sl, target, qty
                )

        # ── Wait for next bar ─────────────────────────────────────────────────
        log.info(f"[{now.strftime('%H:%M')}] Scan done | "
                 f"Positions: {len(book.positions)} | "
                 f"Daily P&L: ₹{book.daily_pnl:+.2f}")
        time.sleep(60)   # poll every 60 seconds

    # ── End of day summary ────────────────────────────────────────────────────
    s = book.summary()
    log.info(f"\n{'='*50}")
    log.info(f"  EOD SUMMARY")
    log.info(f"  Trades today : {s['trades_today']}")
    log.info(f"  Daily P&L    : ₹{s['daily_pnl']:+.2f}")
    log.info(f"{'='*50}")

    with open(f"logs/eod_{today}.json", "w") as f:
        json.dump(s, f, indent=2, default=str)


# ─── CLI ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper", action="store_true", default=True)
    parser.add_argument("--live",  action="store_true", default=False)
    args = parser.parse_args()

    if args.live:
        confirm = input("\n⚠️  LIVE MODE — real money at risk. Type YES to confirm: ")
        if confirm.strip() != "YES":
            print("Aborted."); exit(0)

    run_trading_loop(paper=not args.live)
