"""
NSE Paper Trading Configuration
Mean Reversion Version B with Fix A+C
"""

# Portfolio Configuration
PORTFOLIO_SIZE = 100000
MAX_RISK_PER_TRADE = 4000
MAX_OPEN_TRADES = 3
MAX_EXPOSURE_PCT = 0.60

# Risk Management
ATR_SL_MULT = 2.0
ATR_TP_MULT = 3.0
FORWARD_BARS = 10
MAX_TRADE_PNL_PCT = 0.15

# Indicator Parameters
RSI_PERIOD = 7
RSI_OVERSOLD = 25
RSI_OVERBOUGHT = 75
RSI_NEUTRAL = 50
ADX_THRESHOLD = 30
SMA_LONG_TERM = 200
SMA_SHORT_TERM = 10
MIN_AVG_VOLUME = 500000

# Telegram Configuration
TELEGRAM_CHAT_ID = 8541952881

# Sector Mapping for Diversification (Fix A)
SECTOR_MAPPING = {
    'Banking': ['HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK', 'INDUSINDBK'],
    'IT': ['TCS', 'INFY', 'HCLTECH', 'WIPRO', 'TECHM', 'LTIM'],
    'Pharma': ['SUNPHARMA', 'DIVISLAB', 'DRREDDY', 'CIPLA', 'APOLLOHOSP'],
    'Auto': ['MARUTI', 'EICHERMOT', 'M&M', 'BAJAJ-AUTO', 'HEROMOTOCO'],
    'FMCG': ['HINDUNILVR', 'ITC', 'NESTLEIND', 'BRITANNIA', 'TATACONSUM'],
    'Metal': ['JSWSTEEL', 'TATASTEEL', 'HINDALCO'],
    'Energy': ['RELIANCE', 'ONGC', 'BPCL', 'NTPC', 'POWERGRID'],
    'Infra': ['LT', 'GRASIM', 'ULTRACEMCO', 'ADANIENT', 'ADANIPORTS'],
    'Insurance': ['HDFCLIFE', 'SBILIFE', 'BAJFINANCE', 'BAJAJFINSV'],
    'Telecom': ['BHARTIARTL'],
    'Other': ['ASIANPAINT', 'TITAN', 'COALINDIA', 'SHRIRAMFIN']
}

# Nifty 50 Symbols (from historical data - verified 49 stocks)
NIFTY50_SYMBOLS = [
    'ADANIENT', 'ADANIPORTS', 'APOLLOHOSP', 'ASIANPAINT', 'AXISBANK',
    'BAJAJ-AUTO', 'BAJAJFINSV', 'BAJFINANCE', 'BHARTIARTL', 'BPCL',
    'BRITANNIA', 'CIPLA', 'COALINDIA', 'DIVISLAB', 'DRREDDY',
    'EICHERMOT', 'GRASIM', 'HCLTECH', 'HDFCBANK', 'HDFCLIFE',
    'HEROMOTOCO', 'HINDALCO', 'HINDUNILVR', 'ICICIBANK', 'INDUSINDBK',
    'INFY', 'ITC', 'JSWSTEEL', 'KOTAKBANK', 'LT',
    'LTIM', 'MARUTI', 'M&M', 'NESTLEIND', 'NTPC',
    'ONGC', 'POWERGRID', 'RELIANCE', 'SBILIFE', 'SBIN',
    'SHRIRAMFIN', 'SUNPHARMA', 'TATACONSUM', 'TATASTEEL', 'TCS',
    'TECHM', 'TITAN', 'ULTRACEMCO', 'WIPRO'
]

# SYMBOL_MAP: Maps internal names to yfinance tickers
SYMBOL_MAP = {
    'ADANIENT': 'ADANIENT.NS',
    'ADANIPORTS': 'ADANIPORTS.NS',
    'APOLLOHOSP': 'APOLLOHOSP.NS',
    'ASIANPAINT': 'ASIANPAINT.NS',
    'AXISBANK': 'AXISBANK.NS',
    'BAJAJ-AUTO': 'BAJAJ-AUTO.NS',
    'BAJAJFINSV': 'BAJAJFINSV.NS',
    'BAJFINANCE': 'BAJFINANCE.NS',
    'BHARTIARTL': 'BHARTIARTL.NS',
    'BPCL': 'BPCL.NS',
    'BRITANNIA': 'BRITANNIA.NS',
    'CIPLA': 'CIPLA.NS',
    'COALINDIA': 'COALINDIA.NS',
    'DIVISLAB': 'DIVISLAB.NS',
    'DRREDDY': 'DRREDDY.NS',
    'EICHERMOT': 'EICHERMOT.NS',
    'GRASIM': 'GRASIM.NS',
    'HCLTECH': 'HCLTECH.NS',
    'HDFCBANK': 'HDFCBANK.NS',
    'HDFCLIFE': 'HDFCLIFE.NS',
    'HEROMOTOCO': 'HEROMOTOCO.NS',
    'HINDALCO': 'HINDALCO.NS',
    'HINDUNILVR': 'HINDUNILVR.NS',
    'ICICIBANK': 'ICICIBANK.NS',
    'INDUSINDBK': 'INDUSINDBK.NS',
    'INFY': 'INFY.NS',
    'ITC': 'ITC.NS',
    'JSWSTEEL': 'JSWSTEEL.NS',
    'KOTAKBANK': 'KOTAKBANK.NS',
    'LT': 'LT.NS',
    'LTIM': 'LTIM.NS',
    'MARUTI': 'MARUTI.NS',
    'M&M': 'M&M.NS',
    'NESTLEIND': 'NESTLEIND.NS',
    'NTPC': 'NTPC.NS',
    'ONGC': 'ONGC.NS',
    'POWERGRID': 'POWERGRID.NS',
    'RELIANCE': 'RELIANCE.NS',
    'SBILIFE': 'SBILIFE.NS',
    'SBIN': 'SBIN.NS',
    'SHRIRAMFIN': 'SHRIRAMFIN.NS',
    'SUNPHARMA': 'SUNPHARMA.NS',
    'TATACONSUM': 'TATACONSUM.NS',
    'TATASTEEL': 'TATASTEEL.NS',
    'TCS': 'TCS.NS',
    'TECHM': 'TECHM.NS',
    'TITAN': 'TITAN.NS',
    'ULTRACEMCO': 'ULTRACEMCO.NS',
    'WIPRO': 'WIPRO.NS',
}

# Reverse lookup
YFINANCE_TO_INTERNAL = {v: k for k, v in SYMBOL_MAP.items()}

# File Paths
POSITIONS_FILE = '/root/nse_strategy/paper_trading/positions.json'
TRADES_FILE = '/root/nse_strategy/paper_trading/trades.json'
EQUITY_FILE = '/root/nse_strategy/paper_trading/equity.json'
LOGS_DIR = '/root/nse_strategy/paper_trading/logs/'

# Trading Hours (NSE)
MARKET_OPEN = "09:15"
MARKET_CLOSE = "15:30"

# Data Configuration
DATA_LOOKBACK_DAYS = 250
YFINANCE_RETRY_COUNT = 3
YFINANCE_RETRY_DELAY = 60

# Exposure Limits
MAX_EXPOSURE = PORTFOLIO_SIZE * MAX_EXPOSURE_PCT

# Gate Criteria for 30-day evaluation
GATE_CRITERIA = {
    'min_win_rate': 40,
    'min_profit_factor': 1.2,
    'min_expectancy': 30,
    'max_drawdown_pct': 15,
}
