import requests
import pandas as pd
import numpy as np
import time
import os
import json
import hmac
import hashlib
import urllib.parse
from datetime import datetime, timedelta
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==========================================
# MINI HTTP SERVER - giữ Railway không sleep
# ==========================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - SMC Bot dang chay")
    def log_message(self, format, *args): pass

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# ==========================================
# CONFIG & BẢO MẬT (Railway Variables)
# ==========================================
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
BINGX_API_KEY    = os.environ.get("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "")

BINGX_URL = "https://open-api-vst.bingx.com" # Endpoint Demo VST
SYMBOL    = "GOLD-USDT"
INTERVAL  = os.environ.get("INTERVAL", "15m")
RR        = float(os.environ.get("RR", "2.0"))

# ==========================================
# HELPERS & TELEGRAM
# ==========================================
def now_vn():
    return datetime.utcnow() + timedelta(hours=7)

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM] {msg[:80]}...")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        print(f"Telegram loi: {e}")

# ==========================================
# BINGX API CLIENT
# ==========================================
class BingXClient:
    def __init__(self, api_key, secret_key):
        self.api_key = api_key
        self.secret_key = secret_key

    def _get_signature(self, params):
        query_string = urllib.parse.urlencode(params)
        return hmac.new(
            self.secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def place_market_order(self, side, pos_side, quantity, tp=None, sl=None):
        path = "/openApi/swap/v2/trade/order"
        params = {
            "symbol": SYMBOL,
            "side": side,
            "positionSide": pos_side,
            "type": "MARKET",
            "quantity": quantity,
            "timestamp": int(time.time() * 1000),
            "apiKey": self.api_key
        }
        if tp: params["takeProfit"] = json.dumps({"type": "MARKET", "stopPrice": tp, "price": tp})
        if sl: params["stopLoss"] = json.dumps({"type": "MARKET", "stopPrice": sl, "price": sl})
        
        params["signature"] = self._get_signature(params)
        url = f"{BINGX_URL}{path}?{urllib.parse.urlencode(params)}"
        try:
            r = requests.post(url, timeout=15)
            return r.json()
        except Exception as e:
            print(f"BingX loi: {e}")
            return None

    def get_vst_balance(self):
        path = "/openApi/swap/v2/user/balance"
        params = {"timestamp": int(time.time() * 1000), "apiKey": self.api_key}
        params["signature"] = self._get_signature(params)
        try:
            r = requests.get(f"{BINGX_URL}{path}", params=params, timeout=10)
            data = r.json()
            if data.get("code") == 0:
                for asset in data["data"]["balance"]:
                    if asset["asset"] == "VST": return float(asset["balance"])
        except: pass
        return 0.0

bing_client = BingXClient(BINGX_API_KEY, BINGX_SECRET_KEY)

# ==========================================
# DATA FETCH & SMC LOGIC (Giữ nguyên từ bản gốc)
# ==========================================
def fetch_data(interval="15m", candles=500):
    yf_map = {"1m":"1m","5m":"5m","15m":"15m","1h":"60m"}
    yf_interval = yf_map.get(interval, "15m")
    url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
    params = {"interval": yf_interval, "range": "60d", "events": "history"}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        raw = r.json()
        result = raw.get("chart", {}).get("result", [])
        if not result: return None
        ts = result[0]["timestamp"]
        ohlcv = result[0]["indicators"]["quote"][0]
        df = pd.DataFrame({
            "datetime": pd.to_datetime(ts, unit="s") + pd.Timedelta(hours=7),
            "open": ohlcv["open"], "high": ohlcv["high"],
            "low": ohlcv["low"], "close": ohlcv["close"]
        }).dropna().reset_index(drop=True)
        return df.tail(candles).reset_index(drop=True)
    except: return None

def fetch_latest_price():
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "XAUUSDT"}, timeout=5)
        return round(float(r.json()["price"]), 2)
    except: return None

def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def rsi_calc(s, n=14):
    d = s.diff()
    g = d.where(d>0, 0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.where(d<0, 0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - (100 / (1 + g/l))

def add_indicators(df):
    df['ema200'] = ema(df['close'], 200)
    df['rsi']    = rsi_calc(df['close'], 14)
    df['tr']     = np.maximum(df['high']-df['low'], np.maximum(abs(df['high']-df['close'].shift(1)), abs(df['low'] -df['close'].shift(1))))
    df['atr']    = df['tr'].ewm(span=14, adjust=False).mean()
    return df

def swing_highs(df, n=3):
    idx = []
    for i in range(n, len(df)-n):
        if all(df['high'].iloc[i]>df['high'].iloc[i-j] for j in range(1,n+1)) and all(df['high'].iloc[i]>df['high'].iloc[i+j] for j in range(1,n+1)):
            idx.append(i)
    return idx

def swing_lows(df, n=3):
    idx = []
    for i in range(n, len(df)-n):
        if all(df['low'].iloc[i]<df['low'].iloc[i-j] for j in range(1,n+1)) and all(df['low'].iloc[i]<df['low'].iloc[i+j] for j in range(1,n+1)):
            idx.append(i)
    return idx

def detect_structure(df, sh, sl, i):
    c = df['close'].iloc[i]
    psh = [h for h in sh if h < i-1]
    psl = [l for l in sl if l < i-1]
    if not psh or not psl: return None
    if c > df['high'].iloc[max(psh)]: return "BULL"
    if c < df['low'].iloc[max(psl)]:  return "BEAR"
    return None

def find_ob(df, sig, i, lookback=20):
    atr = df['atr'].iloc[i]
    start = max(0, i-lookback)
    if sig == "BULL":
        for j in range(i-1, start-1, -1):
            if df['close'].iloc[j] < df['open'].iloc[j]:
                return {'type':'BULL_OB','hi':df['high'].iloc[j],'lo':df['low'].iloc[j],'mid':(df['high'].iloc[j]+df['low'].iloc[j])/2}
    elif sig == "BEAR":
        for j in range(i-1, start-1, -1):
            if df['close'].iloc[j] > df['open'].iloc[j]:
                return {'type':'BEAR_OB','hi':df['high'].iloc[j],'lo':df['low'].iloc[j],'mid':(df['high'].iloc[j]+df['low'].iloc[j])/2}
    return None

def bull_confirm(df, i): return df['close'].iloc[i] > df['open'].iloc[i]
def bear_confirm(df, i): return df['close'].iloc[i] < df['open'].iloc[i]

def sltp_long(df, i, ob):
    entry = ob['mid']
    sl = ob['lo'] - df['atr'].iloc[i]*0.5
    tp = entry + (entry-sl)*RR
    return entry, sl, tp

def sltp_short(df, i, ob):
    entry = ob['mid']
    sl = ob['hi'] + df['atr'].iloc[i]*0.5
    tp = entry - (sl-entry)*RR
    return entry, sl, tp

def scan_signal(df):
    sh = swing_highs(df, n=3); sl = swing_lows(df, n=3)
    for i in range(len(df)-1, len(df)-5, -1):
        sig = detect_structure(df, sh, sl, i)
        if sig:
            ob = find_ob(df, sig, i)
            if ob:
                if sig == "BULL" and bull_confirm(df, i):
                    e, s, t = sltp_long(df, i, ob)
                    return {'side':'LONG', 'entry':e, 'sl':s, 'tp':t, 'candle_time':str(df['datetime'].iloc[i])}
                if sig == "BEAR" and bear_confirm(df, i):
                    e, s, t = sltp_short(df, i, ob)
                    return {'side':'SHORT', 'entry':e, 'sl':s, 'tp':t, 'candle_time':str(df['datetime'].iloc[i])}
    return None

def format_signal_msg(signal, price, tf):
    emoji = "🟢" if signal['side'] == "LONG" else "🔴"
    return (f"{emoji} <b>TÍN HIỆU SMC - GOLD {tf}</b>\n\n"
            f"📌 Lệnh: <b>{signal['side']}</b>\n🎯 Vào: <b>{round(signal['entry'],2)}</b>\n"
            f"🛑 SL: <b>{round(signal['sl'],2)}</b>\n✅ TP: <b>{round(signal['tp'],2)}</b>\n"
            f"<i>⏰ {now_vn().strftime('%H:%M')}</i>")

# ==========================================
# BACKGROUND FETCH
# ==========================================
_df_cache = {"df": None}; _cache_lock = threading.Lock()
def _bg_fetcher():
    while True:
        try:
            df = fetch_data(INTERVAL)
            if df is not None:
                df = add_indicators(df)
                with _cache_lock: _df_cache["df"] = df
        except: pass
        time.sleep(30)

# ==========================================
# MAIN EXECUTION
# ==========================================
bg_thread = threading.Thread(target=_bg_fetcher, daemon=True); bg_thread.start()
time.sleep(5)

print(f"Bot SMC BingX khoi dong | {INTERVAL}")
send_telegram(f"🚀 <b>Bot SMC GOLD đã chuyển sang dùng BingX API (VST)</b>")

last_signal_key = None
last_health_time = now_vn()

while True:
    try:
        with _cache_lock: df = _df_cache["df"]
        if df is None:
            time.sleep(3); continue

        fresh_price = fetch_latest_price() or round(df['close'].iloc[-1], 2)
        signal = scan_signal(df)

        if signal:
            sig_key = f"{signal['side']}_{signal['candle_time']}"
            if sig_key != last_signal_key:
                send_telegram(format_signal_msg(signal, fresh_price, INTERVAL))
                last_signal_key = sig_key

                # Thực thi lệnh BingX
                side = "BUY" if signal['side'] == 'LONG' else "SELL"
                pos_side = signal['side']
                order = bing_client.place_market_order(side, pos_side, 1, signal['tp'], signal['sl'])
                
                if order and order.get("code") == 0:
                    bal = bing_client.get_vst_balance()
                    send_telegram(f"✅ <b>BINGX: Đã đặt lệnh {pos_side}</b>\n💰 VST: <b>{round(bal, 2)}</b>")
                else:
                    msg = order.get("msg", "Error") if order else "Conn Error"
                    send_telegram(f"❌ <b>BINGX LỖI:</b> <code>{msg}</code>")

        if (now_vn() - last_health_time).total_seconds() >= 3600:
            send_telegram(f"🤖 <b>Bot SMC Live</b>\nGiá GOLD: {fresh_price}")
            last_health_time = now_vn()

        time.sleep(5)
    except Exception as e:
        print(f"Loi: {e}"); time.sleep(10)