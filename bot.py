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
# CONFIG & BẢO MẬT
# ==========================================
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
BINGX_API_KEY    = os.environ.get("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "")

BINGX_URL = "https://open-api-vst.bingx.com" # Endpoint dành cho Demo VST
SYMBOL    = "GOLD-USDT"
INTERVAL  = os.environ.get("INTERVAL", "15m")
RR        = float(os.environ.get("RR", "2.0"))

# ==========================================
# BINGX API CLIENT
# ==========================================
class BingXClient:
    def __init__(self, api_key, secret_key):
        self.api_key = api_key
        self.secret_key = secret_key

    def _get_signature(self, params):
        """Tạo chữ ký HMAC-SHA256 theo chuẩn BingX"""
        query_string = urllib.parse.urlencode(params)
        return hmac.new(
            self.secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def place_market_order(self, side, pos_side, quantity, tp=None, sl=None):
        """Đặt lệnh Market kèm TP/SL tự động trên BingX"""
        path = "/openApi/swap/v2/trade/order"
        params = {
            "symbol": SYMBOL,
            "side": side,                  # "BUY" hoặc "SELL"
            "positionSide": pos_side,      # "LONG" hoặc "SHORT"
            "type": "MARKET",
            "quantity": quantity,
            "timestamp": int(time.time() * 1000),
            "apiKey": self.api_key
        }
        
        # Gắn TP/SL trực tiếp vào lệnh
        if tp: params["takeProfit"] = json.dumps({"type": "MARKET", "stopPrice": tp, "price": tp})
        if sl: params["stopLoss"] = json.dumps({"type": "MARKET", "stopPrice": sl, "price": sl})
        
        params["signature"] = self._get_signature(params)
        url = f"{BINGX_URL}{path}?{urllib.parse.urlencode(params)}"
        
        try:
            r = requests.post(url, timeout=15)
            return r.json()
        except Exception as e:
            print(f"Lỗi kết nối BingX: {e}")
            return None

    def get_vst_balance(self):
        """Lấy số dư tài khoản demo VST"""
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
# (Các hàm fetch_data, indicators, SMC, server giữ nguyên như code cũ)
# ==========================================

# ... [Giữ nguyên code từ HealthHandler đến scan_signal] ...

# ==========================================
# MAIN LOOP - Tích hợp BingX
# ==========================================

print(f"Bot SMC BingX Demo khoi dong | {INTERVAL}")
send_telegram(f"🚀 <b>Bot SMC GOLD đã chuyển sang dùng BingX API (VST)</b>")

last_signal_key = None
last_health_time = now_vn()
HEALTH_INTERVAL = 3600 # 1 tiếng check 1 lần

# Khởi động background fetcher
bg_thread = threading.Thread(target=_bg_fetcher, daemon=True)
bg_thread.start()
time.sleep(5)

while True:
    try:
        vn_now = now_vn()
        with _cache_lock:
            df = _df_cache["df"]

        if df is None:
            time.sleep(3)
            continue

        fresh_price = fetch_latest_price() or round(df['close'].iloc[-1], 2)
        signal = scan_signal(df) # Logic quét SMC cũ

        if signal:
            sig_key = f"{signal['side']}_{signal['candle_time']}"
            if sig_key != last_signal_key:
                # 1. Bắn thông báo tín hiệu về Telegram
                send_telegram(format_signal_msg(signal, fresh_price, INTERVAL))
                last_signal_key = sig_key

                # 2. Thực thi lệnh trên BingX Demo thay vì DemoTracker
                side = "BUY" if signal['side'] == 'LONG' else "SELL"
                pos_side = signal['side']
                
                # Quantity mặc định là 1 (tương ứng quy định GOLD trên sàn)
                order = bing_client.place_market_order(
                    side=side, 
                    pos_side=pos_side, 
                    quantity=1, 
                    tp=signal['tp'], 
                    sl=signal['sl']
                )
                
                if order and order.get("code") == 0:
                    balance = bing_client.get_vst_balance()
                    send_telegram(f"✅ <b>BINGX: Đã đặt lệnh {pos_side}</b>\n💰 Số dư VST: <b>{round(balance, 2)}</b>")
                else:
                    msg = order.get("msg", "Lỗi API") if order else "Không kết nối được sàn"
                    send_telegram(f"❌ <b>BINGX LỖI:</b> <code>{msg}</code>")

        # Health Check mỗi tiếng
        if (now_vn() - last_health_time).total_seconds() >= HEALTH_INTERVAL:
            send_telegram(f"🤖 <b>Bot SMC vẫn đang chạy</b>\nGiá GOLD: {fresh_price}")
            last_health_time = now_vn()

        time.sleep(5)

    except Exception as e:
        print(f"Lỗi: {e}")
        time.sleep(60)