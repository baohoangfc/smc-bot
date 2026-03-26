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
DATA_SOURCE      = "BINGX"
SYMBOL           = os.environ.get("BINGX_SYMBOL", "NCCOGOLD2USD-USDT")
INTERVAL         = os.environ.get("INTERVAL", "15m")
RR               = float(os.environ.get("RR", "2.0"))
ORDER_NOTIONAL_USDT = float(os.environ.get("ORDER_NOTIONAL_USDT", "1000"))
LEVERAGE = int(os.environ.get("LEVERAGE", "100"))
MAX_ACTIVE_ORDERS = int(os.environ.get("MAX_ACTIVE_ORDERS", "3"))
MIN_TP_PCT = float(os.environ.get("MIN_TP_PCT", "0.20"))
MIN_SL_PCT = float(os.environ.get("MIN_SL_PCT", "0.20"))
SCALP_RR_TARGET = float(os.environ.get("SCALP_RR_TARGET", "1.4"))
SCALP_RR_MIN = float(os.environ.get("SCALP_RR_MIN", "1.0"))
SCALP_RR_MAX = float(os.environ.get("SCALP_RR_MAX", "1.8"))
SL_BUFFER_PCT = float(os.environ.get("SL_BUFFER_PCT", "0.08"))
SWING_LOOKBACK = int(os.environ.get("SWING_LOOKBACK", "6"))
MIN_RISK_PCT = float(os.environ.get("MIN_RISK_PCT", "0.15"))
MIN_ATR_PCT = float(os.environ.get("MIN_ATR_PCT", "0.03"))
TREND_LOOKBACK = int(os.environ.get("TREND_LOOKBACK", "20"))

def now_vn(): return datetime.utcnow() + timedelta(hours=7)

def format_price(value):
    if value is None:
        return "N/A"
    return f"{float(value):.2f}".rstrip("0").rstrip(".")

def pick_first_float(*values):
    for val in values:
        if val is None:
            continue
        try:
            num = float(val)
            if num > 0:
                return num
        except Exception:
            continue
    return None

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

def enforce_tp_sl_safety(side, entry, tp, sl, last_price):
    """
    Đảm bảo TP/SL hợp lệ đồng thời theo:
    - khoảng cách tối thiểu so với entry (tránh TP quá sát => lời rất ít)
    - quy tắc validate theo giá thị trường hiện tại của sàn
    """
    if entry is None or entry <= 0:
        return sanitize_tp_sl(side, tp, sl, last_price)

    e = float(entry)
    safe_tp = float(tp) if tp is not None else None
    safe_sl = float(sl) if sl is not None else None
    min_gap = 0.5
    min_tp_gap = max(min_gap, e * (MIN_TP_PCT / 100.0))
    min_sl_gap = max(min_gap, e * (MIN_SL_PCT / 100.0))

    if side == "LONG":
        min_tp_from_entry = e + min_tp_gap
        max_sl_from_entry = e - min_sl_gap
        if safe_tp is None or safe_tp < min_tp_from_entry:
            safe_tp = min_tp_from_entry
        if safe_sl is None or safe_sl > max_sl_from_entry:
            safe_sl = max_sl_from_entry
    else:
        max_tp_from_entry = e - min_tp_gap
        min_sl_from_entry = e + min_sl_gap
        if safe_tp is None or safe_tp > max_tp_from_entry:
            safe_tp = max_tp_from_entry
        if safe_sl is None or safe_sl < min_sl_from_entry:
            safe_sl = min_sl_from_entry

    safe_tp, safe_sl = sanitize_tp_sl(side, safe_tp, safe_sl, last_price, min_gap=min_gap)
    return safe_tp, safe_sl

def normalize_tp_sl_by_entry(side, entry, tp, sl):
    """
    Bảo vệ TP/SL theo entry để tránh:
    - TP quá sát giá vào lệnh (reward gần như bằng 0)
    - SL đặt sai phía hoặc quá sát entry
    """
    if entry is None or entry <= 0:
        return tp, sl, False

    e = float(entry)
    safe_tp = float(tp) if tp is not None else None
    safe_sl = float(sl) if sl is not None else None
    changed = False

    min_tp_gap = max(0.5, e * (MIN_TP_PCT / 100.0))
    min_sl_gap = max(0.5, e * (MIN_SL_PCT / 100.0))

    if side == "LONG":
        if safe_sl is None or safe_sl >= e:
            safe_sl = e - min_sl_gap
            changed = True
        risk = max(e - safe_sl, min_sl_gap)
        ideal_tp = e + max(min_tp_gap, risk * RR)
        if safe_tp is None or safe_tp <= e or (safe_tp - e) < min_tp_gap:
            safe_tp = ideal_tp
            changed = True
    else:
        if safe_sl is None or safe_sl <= e:
            safe_sl = e + min_sl_gap
            changed = True
        risk = max(safe_sl - e, min_sl_gap)
        ideal_tp = e - max(min_tp_gap, risk * RR)
        if safe_tp is None or safe_tp >= e or (e - safe_tp) < min_tp_gap:
            safe_tp = ideal_tp
            changed = True

    return round(safe_tp, 2), round(safe_sl, 2), changed

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
        def _normalize(v):
            if isinstance(v, bool):
                return "true" if v else "false"
            return str(v)
        normalized = {k: _normalize(v) for k, v in params.items() if v is not None}
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

    def _public_request(self, path, params=None, timeout=15):
        params = params or {}
        r = requests.get(f"{BINGX_URL}{path}", params=params, timeout=timeout)
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
                tp = pick_first_float(
                    p.get("takeProfit"), p.get("takeProfitPrice"), p.get("tpPrice"), p.get("tp")
                )
                sl = pick_first_float(
                    p.get("stopLoss"), p.get("stopLossPrice"), p.get("slPrice"), p.get("sl")
                )
                return {
                    "side": side,
                    "entry": entry,
                    "quantity": abs(qty),
                    "tp": tp,
                    "sl": sl,
                    "opened_at": now_vn(),
                    "unrealizedProfit": float(p.get("unrealizedProfit", 0) or 0),
                    "positionValue": float(p.get("positionValue", 0) or 0),
                    "markPrice": float(p.get("markPrice", 0) or 0)
                }
        except Exception as e:
            print(f"[WARN] get_open_position exception: {e}")
        return None

    def get_last_price(self):
        """
        Lấy giá mới nhất trực tiếp từ BingX quote API.
        Thử lần lượt các endpoint để tương thích nhiều phiên bản API.
        """
        candidates = [
            "/openApi/swap/v2/quote/price",
            "/openApi/swap/v3/quote/price",
            "/openApi/swap/v1/ticker/price",
        ]
        for path in candidates:
            try:
                data = self._public_request(path, {"symbol": SYMBOL}, timeout=10)
                if data.get("code") != 0:
                    continue
                payload = data.get("data", {})
                # Một số endpoint trả list, một số trả dict
                if isinstance(payload, list):
                    payload = payload[0] if payload else {}
                price = (
                    payload.get("price")
                    or payload.get("close")
                    or payload.get("lastPrice")
                    or payload.get("markPrice")
                )
                if price is not None:
                    return float(price)
            except Exception:
                continue
        return None

    def get_klines(self, interval="15m", limit=500):
        """
        Lấy nến trực tiếp từ BingX thay vì nguồn ngoài để đảm bảo khớp dữ liệu sàn.
        """
        endpoints = [
            "/openApi/swap/v3/quote/klines",
            "/openApi/swap/v2/quote/klines",
        ]
        for path in endpoints:
            try:
                data = self._public_request(path, {"symbol": SYMBOL, "interval": interval, "limit": limit}, timeout=15)
                if data.get("code") != 0:
                    continue
                rows = data.get("data", [])
                if not rows:
                    continue
                # Chuẩn hóa về DataFrame: [ts, open, high, low, close, volume, ...]
                if isinstance(rows[0], list):
                    df = pd.DataFrame(rows)
                    if df.shape[1] < 5:
                        continue
                    ts = pd.to_numeric(df.iloc[:, 0], errors="coerce")
                    # BingX có thể trả ts theo giây/ms
                    if ts.dropna().median() > 1e12:
                        dt = pd.to_datetime(ts, unit="ms")
                    else:
                        dt = pd.to_datetime(ts, unit="s")
                    out = pd.DataFrame({
                        "open": pd.to_numeric(df.iloc[:, 1], errors="coerce"),
                        "high": pd.to_numeric(df.iloc[:, 2], errors="coerce"),
                        "low": pd.to_numeric(df.iloc[:, 3], errors="coerce"),
                        "close": pd.to_numeric(df.iloc[:, 4], errors="coerce"),
                        "datetime": dt + pd.Timedelta(hours=7)
                    })
                    out = out.dropna().tail(limit).reset_index(drop=True)
                    if len(out) > 0:
                        return out
                # fallback nếu data là list dict
                if isinstance(rows[0], dict):
                    out = pd.DataFrame(rows)
                    rename_map = {
                        "openPrice": "open",
                        "highPrice": "high",
                        "lowPrice": "low",
                        "closePrice": "close",
                    }
                    out = out.rename(columns=rename_map)
                    ts_col = None
                    for c in ["time", "timestamp", "openTime"]:
                        if c in out.columns:
                            ts_col = c
                            break
                    if ts_col is None:
                        continue
                    ts = pd.to_numeric(out[ts_col], errors="coerce")
                    if ts.dropna().median() > 1e12:
                        out["datetime"] = pd.to_datetime(ts, unit="ms") + pd.Timedelta(hours=7)
                    else:
                        out["datetime"] = pd.to_datetime(ts, unit="s") + pd.Timedelta(hours=7)
                    for col in ["open", "high", "low", "close"]:
                        out[col] = pd.to_numeric(out[col], errors="coerce")
                    out = out[["open", "high", "low", "close", "datetime"]].dropna().tail(limit).reset_index(drop=True)
                    if len(out) > 0:
                        return out
            except Exception:
                continue
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

    def _build_entry_order_params(self, side, pos_side, quantity, order_type="MARKET", price=None, tp=None, sl=None):
        req = {
            "symbol": SYMBOL,
            "side": side,
            "positionSide": pos_side,
            "type": order_type,
            "quantity": quantity,
            "timestamp": int(time.time() * 1000),
            "recvWindow": 5000
        }
        if order_type == "LIMIT" and price is not None:
            req["price"] = price
            req["timeInForce"] = "GTC"
        if tp is not None:
            req["takeProfit"] = json.dumps(
                {"type": "TAKE_PROFIT_MARKET", "stopPrice": tp, "price": tp},
                separators=(",", ":")
            )
        if sl is not None:
            req["stopLoss"] = json.dumps(
                {"type": "STOP_MARKET", "stopPrice": sl, "price": sl},
                separators=(",", ":")
            )
        return req

    def _extract_code(self, resp):
        try:
            return int(resp.get("code"))
        except Exception:
            return None

    def place_order(self, side, pos_side, quantity, order_type="MARKET", price=None, tp=None, sl=None):
        path = "/openApi/swap/v2/trade/order"

        try:
            params = self._build_entry_order_params(side, pos_side, quantity, order_type, price, tp, sl)
            data = self._signed_request("POST", path, params, timeout=15)

            # Nếu thiếu margin, giảm dần khối lượng và thử lại.
            cur_qty = float(quantity)
            while self._extract_code(data) == 101204 and cur_qty > 0.001:
                cur_qty = round(cur_qty / 2, 4)
                retry_params = self._build_entry_order_params(side, pos_side, cur_qty, order_type, price, tp, sl)
                print(f"[WARN] Insufficient margin, thử lại quantity={cur_qty}")
                data = self._signed_request("POST", path, retry_params, timeout=15)

            return data
        except Exception as e:
            print(f"[ERROR] place_order exception: {e}")
            return None

    def place_market_order(self, side, pos_side, quantity, tp=None, sl=None):
        return self.place_order(side, pos_side, quantity, "MARKET", None, tp, sl)

    def place_limit_order(self, side, pos_side, quantity, price, tp=None, sl=None):
        return self.place_order(side, pos_side, quantity, "LIMIT", price, tp, sl)

    def add_missing_tp_sl(self, pos_side, tp=None, sl=None):
        """
        Nếu vị thế hiện tại chưa có TP/SL trên sàn thì đặt bổ sung ngay.
        """
        try:
            if tp is None and sl is None:
                return {"tp_added": False, "sl_added": False, "position": self.get_open_position()}

            position = self.get_open_position()
            if not position or position.get("side") != pos_side:
                return {"tp_added": False, "sl_added": False, "position": position}

            close_side = "SELL" if pos_side == "LONG" else "BUY"
            path = "/openApi/swap/v2/trade/order"
            result = {"tp_added": False, "sl_added": False, "position": position}

            if position.get("tp") is None and tp is not None:
                tp_params = {
                    "symbol": SYMBOL,
                    "side": close_side,
                    "positionSide": pos_side,
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": tp,
                    "price": tp,
                    "closePosition": "true",
                    "timestamp": int(time.time() * 1000),
                    "recvWindow": 5000
                }
                tp_resp = self._signed_request("POST", path, tp_params, timeout=10)
                result["tp_added"] = self._extract_code(tp_resp) == 0
                if not result["tp_added"]:
                    print(f"[WARN] Add TP thất bại: {tp_resp}")

            if position.get("sl") is None and sl is not None:
                sl_params = {
                    "symbol": SYMBOL,
                    "side": close_side,
                    "positionSide": pos_side,
                    "type": "STOP_MARKET",
                    "stopPrice": sl,
                    "price": sl,
                    "closePosition": "true",
                    "timestamp": int(time.time() * 1000),
                    "recvWindow": 5000
                }
                sl_resp = self._signed_request("POST", path, sl_params, timeout=10)
                result["sl_added"] = self._extract_code(sl_resp) == 0
                if not result["sl_added"]:
                    print(f"[WARN] Add SL thất bại: {sl_resp}")

            result["position"] = self.get_open_position()
            return result
        except Exception as e:
            print(f"[WARN] add_missing_tp_sl exception: {e}")
            return {"tp_added": False, "sl_added": False, "position": self.get_open_position()}

bing_client = BingXClient(BINGX_API_KEY, BINGX_SECRET_KEY)

# ==========================================
# (Các hàm fetch_data, indicators, SMC Logic giữ nguyên)
# ==========================================
def fetch_data(interval="15m", candles=500):
    # Dữ liệu nến dùng cho tín hiệu chỉ lấy từ API BingX (không dùng Yahoo/nguồn khác).
    try:
        return bing_client.get_klines(interval=interval, limit=candles)
    except Exception:
        return None

def add_indicators(df):
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['atr'] = (df['high'] - df['low']).ewm(span=14, adjust=False).mean()
    df['atr_pct'] = (df['atr'] / df['close']) * 100
    return df

def calc_scalp_tp_sl(df, side, entry):
    """
    Tính TP/SL theo hướng scalp ngắn:
    - SL bám cấu trúc swing gần nhất + buffer nhỏ
    - TP theo RR mục tiêu (mặc định ~1.4R) và bị chặn trong [SCALP_RR_MIN, SCALP_RR_MAX]
    """
    if len(df) < max(SWING_LOOKBACK + 2, 10):
        return None, None, None

    recent = df.iloc[-(SWING_LOOKBACK + 2):-1]
    atr_val = float(df['atr'].iloc[-2]) if 'atr' in df.columns and not pd.isna(df['atr'].iloc[-2]) else 0
    buffer_by_pct = float(entry) * (SL_BUFFER_PCT / 100.0)
    buffer = max(0.5, buffer_by_pct, atr_val * 0.1)
    min_risk = max(0.5, float(entry) * (MIN_RISK_PCT / 100.0))

    if side == "LONG":
        structure_sl = float(recent['low'].min()) - buffer
        risk = max(float(entry) - structure_sl, min_risk)
        sl = float(entry) - risk
        rr_used = min(max(SCALP_RR_TARGET, SCALP_RR_MIN), SCALP_RR_MAX)
        tp = float(entry) + (risk * rr_used)
    else:
        structure_sl = float(recent['high'].max()) + buffer
        risk = max(structure_sl - float(entry), min_risk)
        sl = float(entry) + risk
        rr_used = min(max(SCALP_RR_TARGET, SCALP_RR_MIN), SCALP_RR_MAX)
        tp = float(entry) - (risk * rr_used)

    return round(tp, 2), round(sl, 2), rr_used

def scan_signal(df):
    """
    Tín hiệu scalp SMC được siết chặt:
    1) Có bias rõ ràng theo EMA50/EMA200 + cấu trúc gần nhất.
    2) Có sweep thanh khoản (quét đỉnh/đáy swing gần nhất).
    3) Có MSS (close phá cấu trúc ngược lại sau sweep).
    4) ATR tối thiểu để tránh vùng nhiễu quá thấp.
    """
    min_bars = max(220, SWING_LOOKBACK + TREND_LOOKBACK + 5)
    if len(df) < min_bars:
        return None

    last_closed = df.iloc[-2]
    prev_closed = df.iloc[-3]
    recent = df.iloc[-(SWING_LOOKBACK + 3):-2]
    if len(recent) < SWING_LOOKBACK:
        return None

    atr_pct = float(df['atr_pct'].iloc[-2]) if 'atr_pct' in df.columns and not pd.isna(df['atr_pct'].iloc[-2]) else 0.0
    if atr_pct < MIN_ATR_PCT:
        return None

    swing_high = float(recent['high'].max())
    swing_low = float(recent['low'].min())
    dealing_range_mid = (swing_high + swing_low) / 2.0

    # HTF bias mô phỏng bằng EMA + cấu trúc gần nhất để giảm lệnh ngược xu hướng.
    ema50 = float(df['ema50'].iloc[-2])
    ema200 = float(df['ema200'].iloc[-2])
    recent_trend_high = float(df['high'].iloc[-(TREND_LOOKBACK + 2):-2].max())
    recent_trend_low = float(df['low'].iloc[-(TREND_LOOKBACK + 2):-2].min())
    close_price = float(last_closed['close'])

    bullish_bias = ema50 > ema200 and close_price > ema50 and close_price > recent_trend_low
    bearish_bias = ema50 < ema200 and close_price < ema50 and close_price < recent_trend_high

    # Sweep + MSS logic
    liquidity_sweep_low = float(last_closed['low']) < swing_low and close_price > swing_low
    liquidity_sweep_high = float(last_closed['high']) > swing_high and close_price < swing_high
    mss_bull = close_price > float(prev_closed['high'])
    mss_bear = close_price < float(prev_closed['low'])

    # Discount/Premium entry filter (SMC dealing range)
    in_discount = close_price <= dealing_range_mid
    in_premium = close_price >= dealing_range_mid

    if bullish_bias and liquidity_sweep_low and mss_bull and in_discount:
        e = round(close_price, 2)
        tp, sl, rr_used = calc_scalp_tp_sl(df, "LONG", e)
        if tp is None or sl is None:
            return None
        return {
            'side': 'LONG',
            'entry': e,
            'sl': sl,
            'tp': tp,
            'rr': rr_used,
            'candle_time': str(last_closed['datetime'])
        }

    if bearish_bias and liquidity_sweep_high and mss_bear and in_premium:
        e = round(close_price, 2)
        tp, sl, rr_used = calc_scalp_tp_sl(df, "SHORT", e)
        if tp is None or sl is None:
            return None
        return {
            'side': 'SHORT',
            'entry': e,
            'sl': sl,
            'tp': tp,
            'rr': rr_used,
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

def format_signal_msg(signal, order_label=None):
    emoji = "🟢" if signal["side"] == "LONG" else "🔴"
    side_text = "MUA (LONG)" if signal["side"] == "LONG" else "BÁN (SHORT)"
    rr_value = signal.get("rr", SCALP_RR_TARGET)
    rr_text = f"1:{rr_value:.1f}"
    order_line = f"🆔 Mã lệnh  : <b>{order_label}</b>\n" if order_label else ""
    return (
        f"{emoji} <b>TÍN HIỆU SMC - {SYMBOL} {INTERVAL}</b>\n\n"
        f"{order_line}"
        f"📌 Lệnh      : <b>{side_text}</b>\n"
        f"💰 Giá hiện tại : <b>{format_price(signal['entry'])}</b>\n"
        f"🎯 Vào lệnh  : <b>{format_price(signal['entry'])}</b>\n"
        f"🛑 Cắt lỗ    : <b>{format_price(signal['sl'])}</b>\n"
        f"✅ Chốt lời  : <b>{format_price(signal['tp'])}</b>\n"
        f"📊 R:R       : <b>{rr_text}</b>\n\n"
        f"💵 Số dư VST : <b>{get_vst_balance_text()}</b>\n"
        f"🔌 Nguồn dữ liệu: <b>{DATA_SOURCE}</b>\n"
        f"⏰ <b>{format_vn_time(signal['candle_time'])} (GMT+7)</b>\n"
        "⚠️ <i>Chỉ tham khảo, tự xác nhận trước khi vào lệnh</i>"
    )

def format_status_msg(last_price, candle_time):
    next_time = pd.to_datetime(candle_time) + timedelta(minutes=interval_to_minutes(INTERVAL))
    return (
        f"🤖 <b>SMC Bot - Cập nhật {format_vn_time(candle_time, '%H:%M')} (GMT+7)</b>\n\n"
        f"Giá {SYMBOL} : <b>{format_price(last_price)}</b>\n"
        f"Khung TG    : <b>{INTERVAL}</b>\n"
        f"Nguồn dữ liệu: <b>{DATA_SOURCE}</b>\n"
        f"Số dư VST   : <b>{get_vst_balance_text()}</b>\n"
        "Trạng thái  : ✅ <b>Đang chạy</b>\n\n"
        "⏳ Chưa có tín hiệu. Đang theo dõi...\n\n"
        f"Cập nhật tiếp theo lúc <b>{format_vn_time(next_time, '%H:%M')}</b>"
    )

def format_order_result_msg(signal, order_result, order_label=None, filled_entry=None):
    order_id = (order_result or {}).get("data", {}).get("order", {}).get("orderId", "N/A")
    entry_to_show = filled_entry if filled_entry is not None else signal.get("entry")
    order_line = f"🆔 Mã lệnh  : <b>{order_label}</b>\n" if order_label else ""
    return (
        "🟢 <b>DEMO - Đặt lệnh thị trường</b>\n\n"
        f"{order_line}"
        f"📌 Lệnh     : <b>{'MUA (LONG)' if signal['side'] == 'LONG' else 'BÁN (SHORT)'}</b>\n"
        f"🎯 Entry    : <b>{format_price(entry_to_show)}</b>\n"
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
    pnl = position.get("unrealizedProfit")
    if pnl is None:
        if side == "LONG":
            pnl = (last_price - entry) * qty
        else:
            pnl = (entry - last_price) * qty
    notional_base = position.get("positionValue") or ORDER_NOTIONAL_USDT
    pnl_pct = (pnl / notional_base) * 100 if notional_base else 0
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    price_to_show = position.get("markPrice") or last_price
    tp_text = format_price(position.get("tp")) if position.get("tp") is not None else "Chưa có"
    sl_text = format_price(position.get("sl")) if position.get("sl") is not None else "Chưa có"
    order_label = position.get("label", "LỆNH")
    return (
        f"{pnl_emoji} <b>Theo dõi lệnh mỗi 1 phút</b>\n\n"
        f"🆔 Mã lệnh  : <b>{order_label}</b>\n"
        f"📌 Lệnh      : <b>{'MUA (LONG)' if side == 'LONG' else 'BÁN (SHORT)'}</b>\n"
        f"🎯 Entry     : <b>{format_price(entry)}</b>\n"
        f"🛑 Cắt lỗ    : <b>{sl_text}</b>\n"
        f"✅ Chốt lời  : <b>{tp_text}</b>\n"
        f"💰 Giá hiện tại: <b>{format_price(price_to_show)}</b>\n"
        f"📦 Khối lượng : <b>{qty}</b>\n"
        f"💵 PnL tạm tính: <b>{pnl:+.2f} USDT ({pnl_pct:+.2f}%)</b>\n"
        f"⏰ <b>{now_vn().strftime('%d/%m/%Y %H:%M')} (GMT+7)</b>"
    )

def calc_live_pnl(position, last_price):
    side = position.get("side")
    qty = float(position.get("quantity", 0) or 0)
    entry = float(position.get("entry", 0) or 0)
    if side == "LONG":
        return (last_price - entry) * qty
    return (entry - last_price) * qty

def decide_positions_to_close(active_positions, incoming_side, live_price):
    if not active_positions:
        return []

    removable = []
    opposite_positions = [p for p in active_positions if p.get("side") != incoming_side]
    if opposite_positions:
        worst_opposite = min(opposite_positions, key=lambda p: calc_live_pnl(p, live_price))
        removable.append(worst_opposite)

    remaining = len(active_positions) - len(removable)
    if remaining >= MAX_ACTIVE_ORDERS:
        candidates = [p for p in active_positions if p not in removable]
        if candidates:
            worst_any = min(candidates, key=lambda p: calc_live_pnl(p, live_price))
            removable.append(worst_any)

    uniq = []
    seen = set()
    for pos in removable:
        label = pos.get("label")
        if label in seen:
            continue
        seen.add(label)
        uniq.append(pos)
    return uniq

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
existing_position = bing_client.get_open_position()
active_positions = []
order_seq = 0
if existing_position:
    order_seq += 1
    existing_position["label"] = f"LỆNH #{order_seq}"
    active_positions.append(existing_position)
if not active_positions:
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
        live_price = bing_client.get_last_price()
        if live_price is None:
            live_price = float(last_closed["close"])
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
                removable_positions = decide_positions_to_close(active_positions, signal["side"], float(live_price))
                for pos in removable_positions:
                    active_positions = [x for x in active_positions if x.get("label") != pos.get("label")]
                    pnl_snapshot = calc_live_pnl(pos, float(live_price))
                    send_telegram(
                        "🔄 <b>Điều chỉnh danh mục lệnh</b>\n"
                        f"Đóng theo phân tích: <b>{pos.get('label')}</b> ({pos.get('side')})\n"
                        f"PnL tạm tính khi đóng: <b>{pnl_snapshot:+.2f} USDT</b>\n"
                        f"Lý do: ưu tiên tín hiệu mới {signal['side']} và giữ tối đa {MAX_ACTIVE_ORDERS} lệnh."
                    )
                if len(active_positions) >= MAX_ACTIVE_ORDERS:
                    print("[INFO] Đã đạt tối đa số lệnh giữ, bỏ qua tín hiệu mới.")
                    last_signal_key = sig_key
                    time.sleep(10)
                    continue
                order_seq += 1
                order_label = f"LỆNH #{order_seq}"
                send_telegram(format_signal_msg(signal, order_label))
                last_signal_key = sig_key
                # Đặt lệnh
                last_price = float(live_price)
                order_signal = dict(signal)
                order_signal["tp"], order_signal["sl"], levels_changed = normalize_tp_sl_by_entry(
                    order_signal["side"], last_price, order_signal.get("tp"), order_signal.get("sl")
                )
                pre_safe_tp, pre_safe_sl = order_signal["tp"], order_signal["sl"]
                order_signal["tp"], order_signal["sl"] = enforce_tp_sl_safety(
                    order_signal["side"], last_price, order_signal["tp"], order_signal["sl"], last_price
                )
                levels_changed = levels_changed or (
                    pre_safe_tp != order_signal["tp"] or pre_safe_sl != order_signal["sl"]
                )
                if levels_changed:
                    send_telegram(
                        "🛠️ <b>Đã hiệu chỉnh TP/SL trước khi vào lệnh</b>\n"
                        f"TP: <b>{format_price(order_signal['tp'])}</b> | "
                        f"SL: <b>{format_price(order_signal['sl'])}</b>\n"
                        f"Tiêu chí: TP tối thiểu {MIN_TP_PCT:.2f}% và SL tối thiểu {MIN_SL_PCT:.2f}% so với entry."
                    )
                quantity = calc_order_quantity(last_price, ORDER_NOTIONAL_USDT)
                order = bing_client.place_market_order("BUY" if signal['side']=='LONG' else "SELL", 
                                                       signal['side'], quantity, order_signal['tp'], order_signal['sl'])
                print(f"Order Result: {order}")
                if order and order.get("code") == 0:
                    fill_price = extract_order_avg_price(order, last_price)
                    send_telegram(format_order_result_msg(order_signal, order, order_label, fill_price))
                    protection_result = bing_client.add_missing_tp_sl(
                        signal["side"], order_signal.get("tp"), order_signal.get("sl")
                    )
                    exchange_pos = (protection_result or {}).get("position")
                    if exchange_pos:
                        has_tp = exchange_pos.get("tp") is not None
                        has_sl = exchange_pos.get("sl") is not None
                        if not (has_tp and has_sl):
                            send_telegram(
                                "⚠️ <b>Cảnh báo:</b> Lệnh đã khớp nhưng chưa thấy đủ TP/SL trên sàn.\n"
                                f"TP: <b>{'Có' if has_tp else 'Thiếu'}</b> | "
                                f"SL: <b>{'Có' if has_sl else 'Thiếu'}</b>\n"
                                "Vui lòng kiểm tra lại trên BingX."
                            )
                        elif protection_result.get("tp_added") or protection_result.get("sl_added"):
                            send_telegram(
                                "🛡️ <b>Đã bổ sung TP/SL sau khi vào lệnh</b>\n"
                                f"TP thêm mới: <b>{'Có' if protection_result.get('tp_added') else 'Không'}</b> | "
                                f"SL thêm mới: <b>{'Có' if protection_result.get('sl_added') else 'Không'}</b>"
                            )

                    active_positions.append({
                        "label": order_label,
                        "side": signal["side"],
                        "entry": fill_price,
                        "quantity": float(quantity),
                        "tp": exchange_pos.get("tp") if exchange_pos and exchange_pos.get("tp") is not None else order_signal.get("tp"),
                        "sl": exchange_pos.get("sl") if exchange_pos and exchange_pos.get("sl") is not None else order_signal.get("sl"),
                        "opened_at": now_vn()
                    })
                    last_pnl_notify_ts = 0
                else:
                    err_msg = (order or {}).get("msg", "Không rõ lỗi")
                    send_telegram(
                        "❌ <b>Đặt lệnh thất bại</b>\n"
                        f"🆔 Mã lệnh: <b>{order_label}</b>\n"
                        f"Lý do: <b>{err_msg}</b>"
                    )
        elif candle_time != last_status_candle:
            send_telegram(format_status_msg(live_price, candle_time))
            last_status_candle = candle_time

        # Nếu đã vào lệnh, gửi noti lời/lỗ mỗi 1 phút cho từng lệnh.
        if active_positions:
            now_ts = time.time()
            if now_ts - last_pnl_notify_ts >= 60:
                exchange_pos = bing_client.get_open_position()
                if not exchange_pos:
                    send_telegram("✅ <b>Không còn vị thế mở trên BingX</b>\nXóa danh sách lệnh đang theo dõi.")
                    active_positions = []
                    last_pnl_notify_ts = now_ts
                    time.sleep(10)
                    continue

                for pos in list(active_positions):
                    if pos.get("tp") is not None:
                        if pos["side"] == "LONG" and float(live_price) >= float(pos["tp"]):
                            send_telegram(f"🏁 <b>{pos.get('label')} đã chạm TP</b>")
                            active_positions.remove(pos)
                            continue
                        if pos["side"] == "SHORT" and float(live_price) <= float(pos["tp"]):
                            send_telegram(f"🏁 <b>{pos.get('label')} đã chạm TP</b>")
                            active_positions.remove(pos)
                            continue
                    if pos.get("sl") is not None:
                        if pos["side"] == "LONG" and float(live_price) <= float(pos["sl"]):
                            send_telegram(f"🛑 <b>{pos.get('label')} đã chạm SL</b>")
                            active_positions.remove(pos)
                            continue
                        if pos["side"] == "SHORT" and float(live_price) >= float(pos["sl"]):
                            send_telegram(f"🛑 <b>{pos.get('label')} đã chạm SL</b>")
                            active_positions.remove(pos)
                            continue
                    send_telegram(format_pnl_msg(pos, float(live_price)))
                last_pnl_notify_ts = now_ts

        time.sleep(10)
    except Exception as e:
        print(f"Lỗi Main Loop: {e}")
        time.sleep(10)
