import requests
import pandas as pd
import numpy as np
import time
import os
import json
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
SYMBOL           = os.environ.get("BINGX_SYMBOL", "NCCOGOLD2USD-USDT")
INTERVAL         = os.environ.get("INTERVAL", "15m")
RR               = float(os.environ.get("RR", "2.0"))
ORDER_NOTIONAL_USDT = float(os.environ.get("ORDER_NOTIONAL_USDT", "1000"))
LEVERAGE = int(os.environ.get("LEVERAGE", "100"))

def now_vn(): return datetime.utcnow() + timedelta(hours=7)

def format_price(value):
    if value is None:
        return "N/A"
    return f"{float(value):.2f}".rstrip("0").rstrip(".")

def format_vn_time(dt_value, fmt="%d/%m/%Y %H:%M"):
    dt = pd.to_datetime(dt_value)
    return dt.strftime(fmt)

def interval_to_minutes(interval):
    mapping = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
    return mapping.get(interval, 15)

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                       json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except: pass

def calc_order_quantity(entry_price, notional_usdt=ORDER_NOTIONAL_USDT):
    """
    quantity theo notional cố định (USDT): qty = notional / entry_price.
    Ví dụ notional=1000 thì mỗi lệnh luôn trị giá ~1000 USDT (đã bao gồm leverage theo cấu hình tài khoản).
    """
    if entry_price is None or entry_price <= 0:
        return 0
    qty = notional_usdt / float(entry_price)
    return round(max(qty, 0), 4)

def sanitize_tp_sl(side, tp, sl, last_price, min_gap=0.5):
    """
    Chuẩn hóa TP/SL để tránh lỗi validate kiểu:
    - LONG: TP phải > Last Price, SL phải < Last Price
    - SHORT: TP phải < Last Price, SL phải > Last Price
    """
    if last_price is None:
        return tp, sl

    lp = float(last_price)
    safe_tp = float(tp) if tp is not None else None
    safe_sl = float(sl) if sl is not None else None

    if side == "LONG":
        if safe_tp is not None and safe_tp <= lp:
            safe_tp = lp + min_gap
        if safe_sl is not None and safe_sl >= lp:
            safe_sl = lp - min_gap
    else:
        if safe_tp is not None and safe_tp >= lp:
            safe_tp = lp - min_gap
        if safe_sl is not None and safe_sl <= lp:
            safe_sl = lp + min_gap

    if safe_tp is not None:
        safe_tp = round(safe_tp, 2)
    if safe_sl is not None:
        safe_sl = round(safe_sl, 2)
    return safe_tp, safe_sl

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

    def get_balance_info(self, asset_name="VST"):
        """Lấy thông tin số dư chi tiết (balance/equity/availableMargin)."""
        path = "/openApi/swap/v2/user/balance"
        params = {"timestamp": int(time.time() * 1000), "recvWindow": 5000}
        try:
            data = self._signed_request("GET", path, params, timeout=10)
            print(f"[DEBUG] BingX Balance Response: {data}")
            if data.get("code") == 0:
                balances = data.get("data", {}).get("balance", [])
                if isinstance(balances, dict):
                    balances = [balances]
                for asset in balances:
                    if asset.get("asset") == asset_name:
                        return {
                            "balance": float(asset.get("balance", 0) or 0),
                            "equity": float(asset.get("equity", 0) or 0),
                            "availableMargin": float(asset.get("availableMargin", 0) or 0),
                            "usedMargin": float(asset.get("usedMargin", 0) or 0),
                        }
            else:
                print(f"[ERROR] BingX trả về lỗi: {data.get('msg')}")
        except Exception as e:
            print(f"[ERROR] Lỗi kết nối lấy số dư: {e}")
        return {"balance": 0.0, "equity": 0.0, "availableMargin": 0.0, "usedMargin": 0.0}

    def get_vst_balance(self):
        return self.get_balance_info("VST").get("balance", 0.0)

    def get_open_position(self):
        """
        Lấy vị thế đang mở của SYMBOL để tránh vào lệnh trùng/đóng lệnh ngoài ý muốn khi restart bot.
        """
        path = "/openApi/swap/v2/user/positions"
        params = {"symbol": SYMBOL, "timestamp": int(time.time() * 1000), "recvWindow": 5000}
        try:
            data = self._signed_request("GET", path, params, timeout=10)
            if data.get("code") != 0:
                return None
            positions = data.get("data", [])
            if isinstance(positions, dict):
                positions = [positions]
            for p in positions:
                qty = float(p.get("positionAmt", 0) or 0)
                if qty == 0:
                    continue
                side = "LONG" if qty > 0 else "SHORT"
                entry = float(p.get("avgPrice", 0) or 0)
                return {"side": side, "entry": entry, "quantity": abs(qty), "opened_at": now_vn()}
        except Exception as e:
            print(f"[WARN] get_open_position exception: {e}")
        return None

    def set_leverage(self, side="LONG", leverage=100):
        path = "/openApi/swap/v2/trade/leverage"
        params = {
            "symbol": SYMBOL,
            "side": side,
            "leverage": int(leverage),
            "timestamp": int(time.time() * 1000),
            "recvWindow": 5000
        }
        try:
            data = self._signed_request("POST", path, params, timeout=10)
            print(f"[INFO] Set leverage {side} x{leverage}: {data}")
            return data
        except Exception as e:
            print(f"[WARN] set_leverage {side} exception: {e}")
            return None

    def place_market_order(self, side, pos_side, quantity, tp=None, sl=None):
        path = "/openApi/swap/v2/trade/order"
        params = {
            "symbol": SYMBOL, "side": side, "positionSide": pos_side,
            "type": "MARKET", "quantity": quantity,
            "timestamp": int(time.time() * 1000), "recvWindow": 5000
        }
        if tp is not None:
            params["takeProfit"] = json.dumps(
                {"type": "TAKE_PROFIT_MARKET", "stopPrice": tp, "price": tp},
                separators=(",", ":")
            )
        if sl is not None:
            params["stopLoss"] = json.dumps(
                {"type": "STOP_MARKET", "stopPrice": sl, "price": sl},
                separators=(",", ":")
            )

        def _extract_code(resp):
            try:
                return int(resp.get("code"))
            except Exception:
                return None

        try:
            data = self._signed_request("POST", path, params, timeout=15)

            if _extract_code(data) != 0 and (tp is not None or sl is not None):
                print(f"[WARN] Đặt lệnh kèm TP/SL lỗi, thử lại không kèm TP/SL: {data}")
                params = {
                    "symbol": SYMBOL, "side": side, "positionSide": pos_side,
                    "type": "MARKET", "quantity": quantity,
                    "timestamp": int(time.time() * 1000), "recvWindow": 5000
                }
                data = self._signed_request("POST", path, params, timeout=15)

            # Nếu thiếu margin, giảm dần khối lượng và thử lại.
            cur_qty = float(quantity)
            while _extract_code(data) == 101204 and cur_qty > 0.001:
                cur_qty = round(cur_qty / 2, 4)
                retry_params = {
                    "symbol": SYMBOL, "side": side, "positionSide": pos_side,
                    "type": "MARKET", "quantity": cur_qty,
                    "timestamp": int(time.time() * 1000), "recvWindow": 5000
                }
                print(f"[WARN] Insufficient margin, thử lại quantity={cur_qty}")
                data = self._signed_request("POST", path, retry_params, timeout=15)

            return data
        except Exception as e:
            print(f"[ERROR] place_market_order exception: {e}")
            return None

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
    # Chỉ dùng nến đã đóng để tránh spam noti khi nến hiện tại còn chạy.
    if len(df) < 6: return None
    last_closed = df.iloc[-2]
    if last_closed['close'] > df['ema200'].iloc[-2]: # Giả định tín hiệu Long đơn giản
        e = round(float(last_closed['close']), 2)
        return {
            'side': 'LONG',
            'entry': e,
            'sl': round(e - 2, 2),
            'tp': round(e + 4, 2),
            'candle_time': str(last_closed['datetime'])
        }
    return None

def get_vst_balance_text():
    return f"{bing_client.get_vst_balance():.4f} VST"

def format_startup_msg(vst_balance):
    return (
        "🚀 <b>SMC Bot đã khởi động</b>\n"
        f"💵 Số dư: <b>{vst_balance:.4f} VST</b>\n"
        f"🕒 Thời gian: <b>{now_vn().strftime('%d/%m/%Y %H:%M')} (GMT+7)</b>"
    )

def format_signal_msg(signal):
    emoji = "🟢" if signal["side"] == "LONG" else "🔴"
    side_text = "MUA (LONG)" if signal["side"] == "LONG" else "BÁN (SHORT)"
    rr_text = f"1:{RR:.1f}"
    return (
        f"{emoji} <b>TÍN HIỆU SMC - {SYMBOL} {INTERVAL}</b>\n\n"
        f"📌 Lệnh      : <b>{side_text}</b>\n"
        f"💰 Giá hiện tại : <b>{format_price(signal['entry'])}</b>\n"
        f"🎯 Vào lệnh  : <b>{format_price(signal['entry'])}</b>\n"
        f"🛑 Cắt lỗ    : <b>{format_price(signal['sl'])}</b>\n"
        f"✅ Chốt lời  : <b>{format_price(signal['tp'])}</b>\n"
        f"📊 R:R       : <b>{rr_text}</b>\n\n"
        f"💵 Số dư VST : <b>{get_vst_balance_text()}</b>\n"
        f"⏰ <b>{format_vn_time(signal['candle_time'])} (GMT+7)</b>\n"
        "⚠️ <i>Chỉ tham khảo, tự xác nhận trước khi vào lệnh</i>"
    )

def format_status_msg(last_price, candle_time):
    next_time = pd.to_datetime(candle_time) + timedelta(minutes=interval_to_minutes(INTERVAL))
    return (
        f"🤖 <b>SMC Bot - Cập nhật {format_vn_time(candle_time, '%H:%M')} (GMT+7)</b>\n\n"
        f"Giá XAUUSDT : <b>{format_price(last_price)}</b>\n"
        f"Khung TG    : <b>{INTERVAL}</b>\n"
        f"Số dư VST   : <b>{get_vst_balance_text()}</b>\n"
        "Trạng thái  : ✅ <b>Đang chạy</b>\n\n"
        "⏳ Chưa có tín hiệu. Đang theo dõi...\n\n"
        f"Cập nhật tiếp theo lúc <b>{format_vn_time(next_time, '%H:%M')}</b>"
    )

def format_order_result_msg(signal, order_result):
    order_id = (order_result or {}).get("data", {}).get("order", {}).get("orderId", "N/A")
    return (
        "🟢 <b>DEMO - Đặt lệnh thị trường</b>\n\n"
        f"📌 Lệnh     : <b>{'MUA (LONG)' if signal['side'] == 'LONG' else 'BÁN (SHORT)'}</b>\n"
        f"🎯 Entry    : <b>{format_price(signal['entry'])}</b>\n"
        f"🛑 Cắt lỗ   : <b>{format_price(signal['sl'])}</b>\n"
        f"✅ Chốt lời : <b>{format_price(signal['tp'])}</b>\n"
        f"💵 Số dư VST: <b>{get_vst_balance_text()}</b>\n"
        f"🧾 Order ID : <b>{order_id}</b>\n"
        f"📦 Notional : <b>{ORDER_NOTIONAL_USDT:.0f} USDT</b>\n"
        f"⚙️ Leverage  : <b>x{LEVERAGE}</b>\n"
        f"⏰ Thời gian : <b>{now_vn().strftime('%d/%m %H:%M')} (GMT+7)</b>"
    )

def extract_order_avg_price(order_result, fallback_price):
    try:
        avg_price = order_result.get("data", {}).get("order", {}).get("avgPrice")
        if avg_price is not None and float(avg_price) > 0:
            return float(avg_price)
    except Exception:
        pass
    return float(fallback_price)

def format_pnl_msg(position, last_price):
    side = position["side"]
    qty = position["quantity"]
    entry = position["entry"]
    if side == "LONG":
        pnl = (last_price - entry) * qty
    else:
        pnl = (entry - last_price) * qty
    pnl_pct = (pnl / ORDER_NOTIONAL_USDT) * 100 if ORDER_NOTIONAL_USDT else 0
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    return (
        f"{pnl_emoji} <b>Theo dõi lệnh mỗi 1 phút</b>\n\n"
        f"📌 Lệnh      : <b>{'MUA (LONG)' if side == 'LONG' else 'BÁN (SHORT)'}</b>\n"
        f"🎯 Entry     : <b>{format_price(entry)}</b>\n"
        f"💰 Giá hiện tại: <b>{format_price(last_price)}</b>\n"
        f"📦 Khối lượng : <b>{qty}</b>\n"
        f"💵 PnL tạm tính: <b>{pnl:+.2f} USDT ({pnl_pct:+.2f}%)</b>\n"
        f"⏰ <b>{now_vn().strftime('%d/%m/%Y %H:%M')} (GMT+7)</b>"
    )

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
active_position = bing_client.get_open_position()
if not active_position:
    bing_client.set_leverage("LONG", LEVERAGE)
    bing_client.set_leverage("SHORT", LEVERAGE)
send_telegram(format_startup_msg(vst_bal))

last_signal_key = None
last_status_candle = None
last_pnl_notify_ts = 0
bootstrapped_signal = False
while True:
    try:
        with _lock: df = _df_cache["df"]
        if df is None: time.sleep(5); continue

        last_closed = df.iloc[-2]
        candle_time = str(last_closed["datetime"])
        signal = scan_signal(df)
        if signal:
            sig_key = f"{signal['side']}_{signal['candle_time']}"
            # Khi restart bot: ghi nhận tín hiệu hiện tại, chỉ trade từ tín hiệu mới tiếp theo.
            if not bootstrapped_signal:
                last_signal_key = sig_key
                bootstrapped_signal = True
                print(f"[INFO] Bootstrapped signal: {sig_key} - chờ tín hiệu mới để vào lệnh.")
                time.sleep(10)
                continue
            if sig_key != last_signal_key:
                # Nếu còn vị thế mở, không vào thêm lệnh để tránh đóng/mở chồng khi khởi động lại.
                if active_position:
                    print("[INFO] Đang có vị thế mở, bỏ qua tín hiệu mới.")
                    last_signal_key = sig_key
                    time.sleep(10)
                    continue
                send_telegram(format_signal_msg(signal))
                last_signal_key = sig_key
                # Đặt lệnh
                last_price = float(last_closed["close"])
                order_signal = dict(signal)
                order_signal["tp"], order_signal["sl"] = sanitize_tp_sl(
                    order_signal["side"], order_signal["tp"], order_signal["sl"], last_price
                )
                quantity = calc_order_quantity(last_price, ORDER_NOTIONAL_USDT)
                order = bing_client.place_market_order("BUY" if signal['side']=='LONG' else "SELL", 
                                                       signal['side'], quantity, order_signal['tp'], order_signal['sl'])
                send_telegram(format_order_result_msg(order_signal, order))
                print(f"Order Result: {order}")
                if order and order.get("code") == 0:
                    fill_price = extract_order_avg_price(order, last_price)
                    active_position = {
                        "side": signal["side"],
                        "entry": fill_price,
                        "quantity": float(quantity),
                        "opened_at": now_vn()
                    }
                    last_pnl_notify_ts = 0
        elif candle_time != last_status_candle:
            send_telegram(format_status_msg(last_closed["close"], candle_time))
            last_status_candle = candle_time

        # Nếu đã vào lệnh, gửi noti lời/lỗ mỗi 1 phút.
        if active_position:
            now_ts = time.time()
            if now_ts - last_pnl_notify_ts >= 60:
                # Đồng bộ lại trạng thái vị thế từ sàn (đề phòng đã đóng do TP/SL).
                exchange_pos = bing_client.get_open_position()
                if not exchange_pos:
                    send_telegram("✅ <b>Vị thế đã đóng trên BingX</b>\nDừng theo dõi PnL cho lệnh trước.")
                    active_position = None
                    last_pnl_notify_ts = now_ts
                    time.sleep(10)
                    continue
                active_position = exchange_pos
                send_telegram(format_pnl_msg(active_position, float(last_closed["close"])))
                last_pnl_notify_ts = now_ts

        time.sleep(10)
    except Exception as e:
        print(f"Lỗi Main Loop: {e}")
        time.sleep(10)
