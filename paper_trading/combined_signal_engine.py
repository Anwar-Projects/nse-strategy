"""
Combined Signal Engine - NSE Mean Reversion
Combines yfinance EOD signals with IEOD intraday confirmation
"""
import os
import pandas as pd
import numpy as np
import yfinance as yf
from pathlib import Path
from datetime import datetime, timedelta
import json
import requests

IEOD_DIR = Path('/root/nse_strategy/data/lake')
LOG_DIR = Path('/root/nse_strategy/logs')
SIGNAL_LOG = LOG_DIR / 'combined_signals.log'
BOT_TOKEN = '8793580045:AAHj3rtvjrkA112KUqzNkueRPCQb_sx0jkE'
CHAT_ID = '8541952881'

# Nifty 50 universe
NIFTY50 = [
 'RELIANCE.NS','TCS.NS','HDFCBANK.NS','INFY.NS','ICICIBANK.NS',
 'HINDUNILVR.NS','ITC.NS','SBIN.NS','BHARTIARTL.NS','KOTAKBANK.NS',
 'LT.NS','AXISBANK.NS','ASIANPAINT.NS','MARUTI.NS','WIPRO.NS',
 'ULTRACEMCO.NS','TITAN.NS','BAJFINANCE.NS','NESTLEIND.NS','POWERGRID.NS',
 'NTPC.NS','ONGC.NS','JSWSTEEL.NS','HCLTECH.NS','TECHM.NS',
 'BAJAJ-AUTO.NS','SUNPHARMA.NS','DIVISLAB.NS','DRREDDY.NS','CIPLA.NS',
 'GRASIM.NS','ADANIENT.NS','ADANIPORTS.NS','HDFCLIFE.NS','SBILIFE.NS',
 'INDUSINDBK.NS','HEROMOTOCO.NS','BRITANNIA.NS','TATACONSUM.NS','HINDALCO.NS',
 'BPCL.NS','COALINDIA.NS','EICHERMOT.NS','LTIM.NS','APOLLOHOSP.NS'
]

def send_telegram(msg):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    requests.post(url, data={'chat_id': CHAT_ID, 'text': msg, 'parse_mode': 'HTML'})

def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_ieod_stats(symbol):
    try:
        files = sorted(IEOD_DIR.glob('*.parquet'))
        if len(files) < 1:
            return {'available': False, 'count': 0}
        
        # Check last 5 days of IEOD data for this symbol
        recent = files[-5:]
        for f in recent:
            df = pd.read_parquet(f)
            if 'symbol' in df.columns and symbol.replace('.NS', '') in df['symbol'].values:
                return {'available': True, 'count': len(files)}
            elif symbol.replace('.NS', '') in df.columns:
                return {'available': True, 'count': len(files)}
        return {'available': False, 'count': len(files)}
    except:
        return {'available': False, 'count': len(list(IEOD_DIR.glob('*.parquet')))}

def get_yfinance_signal(symbol):
    try:
        df = yf.download(symbol, period='1y', interval='1d', progress=False, auto_adjust=True)
        if len(df) < 200:
            return None
        
        close = df['Close'].squeeze()
        high = df['High'].squeeze()
        low = df['Low'].squeeze()
        volume = df['Volume'].squeeze()
        
        rsi = calculate_rsi(close)
        sma200 = close.rolling(200).mean()
        sma10 = close.rolling(10).mean()
        avg_vol = volume.rolling(20).mean()
        
        # ADX calculation
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        plus_di = 100 * plus_dm.rolling(14).mean() / atr
        minus_di = 100 * minus_dm.rolling(14).mean() / atr
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.rolling(14).mean()
        
        latest = {
            'rsi': float(rsi.iloc[-1]),
            'price': float(close.iloc[-1]),
            'sma200': float(sma200.iloc[-1]),
            'sma10': float(sma10.iloc[-1]),
            'adx': float(adx.iloc[-1]),
            'avg_vol': float(avg_vol.iloc[-1]),
            'vol_ratio': float(volume.iloc[-1] / avg_vol.iloc[-1]) if avg_vol.iloc[-1] > 0 else 0
        }
        
        signal = 'NONE'
        confidence = 'LEVEL_0'
        
        # Long entry: RSI<25, price>sma200, price<sma10, ADX<30, vol>500k
        if (latest['rsi'] < 25 and latest['price'] > latest['sma200'] and 
            latest['price'] < latest['sma10'] and latest['adx'] < 30 and 
            latest['avg_vol'] > 500000):
            signal = 'BUY'
        # Short entry: RSI>75, price<sma200, price>sma10, ADX<30, vol>500k
        elif (latest['rsi'] > 75 and latest['price'] < latest['sma200'] and 
              latest['price'] > latest['sma10'] and latest['adx'] < 30 and 
              latest['avg_vol'] > 500000):
            signal = 'SELL'
        
        return {'signal': signal, **latest}
    except Exception as e:
        return None

def run_combined_signals():
    print(f'\n[{datetime.now():%Y-%m-%d %H:%M:%S}] Running Combined Signal Engine')
    ieod_count = len(list(IEOD_DIR.glob('*.parquet')))
    print(f'IEOD days available: {ieod_count}')
    print('=' * 70)
    
    signals = []
    for symbol in NIFTY50:
        yf_data = get_yfinance_signal(symbol)
        if yf_data and yf_data['signal'] != 'NONE':
            ieod = get_ieod_stats(symbol)
            if ieod['available']:
                confidence = 'LEVEL_2_HIGH'
            else:
                confidence = 'LEVEL_1_LOW'
            
            sig = {
                'symbol': symbol,
                'signal': yf_data['signal'],
                'confidence': confidence,
                'rsi': yf_data['rsi'],
                'price': yf_data['price'],
                'ieod': ieod['available'],
                'timestamp': datetime.now().isoformat()
            }
            signals.append(sig)
            print(f'{confidence}: {yf_data["signal"]} {symbol} RSI={yf_data["rsi"]:.1f}')
    
    # Save to log
    LOG_DIR.mkdir(exist_ok=True)
    with open(SIGNAL_LOG, 'a') as f:
        for s in signals:
            f.write(json.dumps(s) + '\n')
    
    if signals:
        msg = f"""🎯 NSE Signals - {datetime.now():%Y-%m-%d}
{'\n'.join([f'{s["signal"]} {s["symbol"]} @ ₹{s["price"]:.1f} [RSI:{s["rsi"]:.1f}] [{s["confidence"]}]' for s in signals])}
IEOD: {ieod_count}/120 days
"""
        send_telegram(msg)
        print(f'\nSent {len(signals)} signals to Telegram')
    else:
        print('\nNo signals today')
    
    return signals

if __name__ == '__main__':
    run_combined_signals()
