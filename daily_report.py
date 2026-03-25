import json, os, urllib.request
from pathlib import Path
from datetime import date

BASE_DIR = Path("/root/nse_strategy")
LOG_DIR = BASE_DIR / "logs"

def send_telegram(message):
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("Telegram credentials not found in .env")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id" : chat_id,
        "text" : message,
        "parse_mode": "HTML"
    }).encode()
    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        resp = urllib.request.urlopen(req, timeout=10)
        print(f"Telegram sent: HTTP {resp.status}")
    except Exception as e:
        print(f"Telegram failed: {e}")

def main():
    today = date.today().isoformat()
    log_file = LOG_DIR / f"paper_trades_{today}.json"

    # Check last 5 days results for trend
    parquets = sorted(Path(BASE_DIR / "data" / "lake").glob("*.parquet"))
    lake_days = len(parquets)

    # Load meta for model info
    meta_path = BASE_DIR / "models" / "latest" / "meta.json"
    model_name = "Unknown"
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)
        model_name = meta.get("best_model", "Unknown")

    if not log_file.exists():
        msg = (
            f"<b>NSE Strategy — {today}</b>\n"
            f"\n"
            f"No backtest results yet for today.\n"
            f"Check that data feed arrived and was ingested.\n"
            f"\n"
            f"Lake days: {lake_days} | Model: {model_name}"
        )
        send_telegram(msg)
        return

    with open(log_file) as f:
        d = json.load(f)

    wins = d.get("wins", 0)
    losses = d.get("losses", 0)
    total = d.get("total_trades", 0)
    wr = d.get("win_rate", 0)
    pnl = d.get("daily_pnl", 0)
    tdate = d.get("test_date", today)

    # Trend — load last 5 days history
    history_path = LOG_DIR / "metrics_history.json"
    trend_lines = ""
    if history_path.exists():
        with open(history_path) as f:
            history = json.load(f)
        recent = history[-5:]
        if recent:
            trend_lines = "\n<b>Last 5 runs:</b>\n"
            for h in recent:
                arrow = "UP" if h.get("net_pnl", 0) >= 0 else "DN"
                trend_lines += (
                    f"{h['date']} | "
                    f"WR:{h.get('win_rate',0):.0f}% | "
                    f"PF:{h.get('profit_factor',0):.2f} | "
                    f"{arrow}\n"
                )

    # Status emoji
    if wr >= 60:
        status = "EXCELLENT"
        emoji = "EXCEL"
    elif wr >= 55:
        status = "GOOD"
        emoji = "GOOD"
    elif wr >= 50:
        status = "WATCH"
        emoji = "WATCH"
    else:
        status = "ALERT"
        emoji = "ALERT"

    pnl_dir = "UP" if pnl >= 0 else "DOWN"

    msg = (
        f"<b>NSE EOD {pnl_dir} — {tdate}</b>\n"
        f"\n"
        f"Trades : {total}\n"
        f"Wins : {wins} | Losses: {losses}\n"
        f"Win Rate : {wr:.1f}%\n"
        f"Daily PnL : Rs{pnl:+,.0f}\n"
        f"\n"
        f"Status : {emoji} {status}\n"
        f"Model : {model_name}\n"
        f"Lake days : {lake_days}\n"
        f"{trend_lines}"
    )

    print(msg)
    send_telegram(msg)

if __name__ == "__main__":
    main()
