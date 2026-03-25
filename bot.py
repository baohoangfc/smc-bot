import requests
import pandas as pd
import numpy as np
import time
import os
import hmac
import hashlib
from datetime import datetime, timedelta
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==========================================
# 1. HEALTH CHECK SERVER - Đảm bảo Railway không tắt bot
# ==========================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"OK")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, format, *args): pass

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health Check Server started on port {port}")
    server.serve_forever()

# Khởi chạy server ngay lập tức để Railway xác nhận bot còn sống
threading.Thread(target=run_server, daemon=True).start()

# ==========================================
# 2. CONFIG & HELPERS
# ==========================================
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
BINGX_API_KEY    = os.environ.get("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "")
BINGX_URL        = "https://open-api-vst.bingx.com"
SYMBOL           = "GOLD-USDT"
INTERVAL         = os.environ.get("INTERVAL", "15m")
RR               = float(os.environ.get("RR", "2.0"))

def now_vn(): return datetime.utcnow() + timedelta(hours=7)

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                       json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except: pass

# ==========================================
# 3. BINGX API CLIENT (Đã thêm Log để debug số dư)
# ==========================================
class BingXClient:
    def __init__(self, api_key, secret_key):
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()

    def _build_signed_query(self, params):
        """
        BingX yêu cầu ký theo chuỗi query đã sort key.
        Theo sample code, cần ký trên chuỗi key=value chưa URL-encode để
        tránh mismatch giữa chuỗi ký và chuỗi backend verify.
        """
        normalized = {k: str(v) for k, v in params.items() if v is not None}
        query_string = "&".join([f"{k}={normalized[k]}" for k in sorted(normalized.keys())])
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return f"{query_string}&signature={signature}"

    def _signed_request(self, method, path, params, timeout=15):
        headers = {"X-BX-APIKEY": self.api_key, "Content-Type": "application/x-www-form-urlencoded"}
        signed_query = self._build_signed_query(params)
        if method.upper() == "GET":
            r = requests.get(f"{BINGX_URL}{path}?{signed_query}", headers=headers, timeout=timeout)
        else:
            r = requests.post(f"{BINGX_URL}{path}?{signed_query}", headers=headers, timeout=timeout)
        return r.json()

    def get_vst_balance(self):
        """Lấy số dư VST và in phản hồi để debug"""
        path = "/openApi/swap/v2/user/balance"
        params = {"timestamp": int(time.time() * 1000), "recvWindow": 5000}
        try:
            data = self._signed_request("GET", path, params, timeout=10)
            # In ra log Railway để bạn kiểm tra lý do 0.0
            print(f"[DEBUG] BingX Balance Response: {data}")
            
            if data.get("code") == 0:
                balances = data.get("data", {}).get("balance", [])
                if isinstance(balances, dict):
                    balances = [balances]
                for asset in balances:
                    if asset.get("asset") == "VST":
                        return float(asset.get("balance", 0))
            else:
                print(f"[ERROR] BingX trả về lỗi: {data.get('msg')}")
        except Exception as e:
            print(f"[ERROR] Lỗi kết nối lấy số dư: {e}")
        return 0.0

    def place_market_order(self, side, pos_side, quantity, tp=None, sl=None):
        path = "/openApi/swap/v2/trade/order"
        params = {
            "symbol": SYMBOL, "side": side, "positionSide": pos_side,
            "type": "MARKET", "quantity": quantity,
            "timestamp": int(time.time() * 1000), "recvWindow": 5000
        }
        if tp or sl:
            print("[INFO] Bỏ qua TP/SL trong lệnh MARKET để tránh lỗi chữ ký; nên đặt TP/SL bằng endpoint điều kiện riêng sau khi khớp lệnh.")

        try:
            data = self._signed_request("POST", path, params, timeout=15)
            return data
        except: return None

bing_client = BingXClient(BINGX_API_KEY, BINGX_SECRET_KEY)

# ==========================================
# (Các hàm fetch_data, indicators, SMC Logic giữ nguyên)
# ==========================================
def fetch_data(interval="15m", candles=500):
    yf_map = {"1m":"1m","5m":"5m","15m":"15m","1h":"60m"}
    yf_interval = yf_map.get(interval, "15m")
    url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, params={"interval": yf_interval, "range": "60d"}, headers=headers, timeout=15)
        res = r.json().get("chart", {}).get("result", [])[0]
        df = pd.DataFrame({"open": res["indicators"]["quote"][0]["open"], 
                           "high": res["indicators"]["quote"][0]["high"],
                           "low": res["indicators"]["quote"][0]["low"],
                           "close": res["indicators"]["quote"][0]["close"],
                           "datetime": pd.to_datetime(res["timestamp"], unit="s") + pd.Timedelta(hours=7)})
        return df.dropna().tail(candles).reset_index(drop=True)
    except: return None

def add_indicators(df):
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['atr'] = (df['high'] - df['low']).ewm(span=14, adjust=False).mean()
    return df

def scan_signal(df):
    # Logic SMC đơn giản hóa để ví dụ
    if len(df) < 5: return None
    last = df.iloc[-1]
    if last['close'] > df['ema200'].iloc[-1]: # Giả định tín hiệu Long đơn giản
        e = last['close']
        return {'side':'LONG', 'entry':e, 'sl':e-2, 'tp':e+4, 'candle_time':str(last['datetime'])}
    return None

# ==========================================
# 4. BACKGROUND FETCH & MAIN LOOP
# ==========================================
_df_cache = {"df": None}; _lock = threading.Lock()
def _bg_fetcher():
    while True:
        try:
            df = fetch_data(INTERVAL)
            if df is not None:
                df = add_indicators(df)
                with _lock: _df_cache["df"] = df
                print(f"[BG] Updated | Close: {df['close'].iloc[-1]}")
        except: pass
        time.sleep(30)

threading.Thread(target=_bg_fetcher, daemon=True).start()
time.sleep(10) # Đợi dữ liệu lần đầu

vst_bal = bing_client.get_vst_balance()
send_telegram(f"🚀 <b>Bot SMC Khởi động</b>\n💵 Số dư: <b>{vst_bal} VST</b>")

last_signal_key = None
while True:
    try:
        with _lock: df = _df_cache["df"]
        if df is None: time.sleep(5); continue

        signal = scan_signal(df)
        if signal:
            sig_key = f"{signal['side']}_{signal['candle_time']}"
            if sig_key != last_signal_key:
                send_telegram(f"🎯 Tín hiệu: {signal['side']} @ {signal['entry']}")
                last_signal_key = sig_key
                # Đặt lệnh
                order = bing_client.place_market_order("BUY" if signal['side']=='LONG' else "SELL", 
                                                       signal['side'], 1, signal['tp'], signal['sl'])
                print(f"Order Result: {order}")

        time.sleep(10)
    except Exception as e:
        print(f"Lỗi Main Loop: {e}")
        time.sleep(10)
