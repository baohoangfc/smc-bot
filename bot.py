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
ALLOW_FALLBACK_SIGNAL = os.environ.get("ALLOW_FALLBACK_SIGNAL", "true").lower() == "true"
READ_ONLY_MODE = os.environ.get("READ_ONLY_MODE", "false").lower() == "true"
SIGNAL_ENGINE = os.environ.get("SIGNAL_ENGINE", "auto").lower()  # auto | strict | backtest_v5

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

def is_trading_enabled():
    """
    Chỉ cho phép đặt/đóng lệnh khi đủ API key và không bật READ_ONLY_MODE.
    """
    if READ_ONLY_MODE:
        return False
    return bool(BINGX_API_KEY and BINGX_SECRET_KEY)

def has_api_credentials():
    return bool(BINGX_API_KEY and BINGX_SECRET_KEY)

def resolve_signal_engine():
    """
    auto:
    - Nếu bot chỉ cảnh báo (read-only) => ưu tiên backtest_v5 để bắt tín hiệu gần backtest.
    - Nếu bot auto-trade => ưu tiên strict để lọc tín hiệu chặt hơn.
    """
    if SIGNAL_ENGINE in ("strict", "backtest_v5"):
        return SIGNAL_ENGINE
    return "backtest_v5" if not is_trading_enabled() else "strict"

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

def calc_rr_from_levels(side, entry, tp, sl):
    """
    Tính RR thực tế từ entry/tp/sl.
    Trả về None nếu thiếu dữ liệu hoặc thông số không hợp lệ.
    """
    try:
        e = float(entry)
        take_profit = float(tp)
        stop_loss = float(sl)
    except Exception:
        return None

    if e <= 0:
        return None

    if side == "LONG":
        risk = e - stop_loss
        reward = take_profit - e
    else:
        risk = stop_loss - e
        reward = e - take_profit

    if risk <= 0 or reward <= 0:
        return None
    return reward / risk

def format_rr_text(side, entry, tp, sl, fallback_rr=None, decimals=2):
    rr_value = calc_rr_from_levels(side, entry, tp, sl)
    if rr_value is None:
        rr_value = fallback_rr
    if rr_value is None:
        return "N/A"
    return f"1:{float(rr_value):.{decimals}f}"

def align_tp_sl_with_rr(side, entry, tp, sl, rr_target):
    """
    Đồng bộ TP/SL theo RR mục tiêu để tránh lệch RR khi entry thay đổi trước lúc đặt lệnh.
    """
    if entry is None or entry <= 0:
        return tp, sl, False

    e = float(entry)
    safe_tp = float(tp) if tp is not None else None
    safe_sl = float(sl) if sl is not None else None
    changed = False

    rr = rr_target if rr_target and rr_target > 0 else RR
    min_tp_gap = max(0.5, e * (MIN_TP_PCT / 100.0))
    min_sl_gap = max(0.5, e * (MIN_SL_PCT / 100.0))

    if side == "LONG":
        if safe_sl is None or safe_sl >= e:
            safe_sl = e - min_sl_gap
            changed = True
        risk = max(e - safe_sl, min_sl_gap)
        ideal_tp = e + max(min_tp_gap, risk * rr)
        if safe_tp is None or abs(safe_tp - ideal_tp) > 0.01:
            safe_tp = ideal_tp
            changed = True
    else:
        if safe_sl is None or safe_sl <= e:
            safe_sl = e + min_sl_gap
            changed = True
        risk = max(safe_sl - e, min_sl_gap)
        ideal_tp = e - max(min_tp_gap, risk * rr)
        if safe_tp is None or abs(safe_tp - ideal_tp) > 0.01:
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
        if not has_api_credentials():
            return {"balance": 0.0, "equity": 0.0, "availableMargin": 0.0, "usedMargin": 0.0}
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
        if not has_api_credentials():
            return None
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

            # Nếu lệnh đính kèm TP/SL bị từ chối, thử vào lệnh market trước rồi sẽ gắn TP/SL sau.
            if self._extract_code(data) != 0 and (tp is not None or sl is not None):
                print(f"[WARN] place_order with TP/SL failed, retry market only: {data}")
                retry_plain = self._build_entry_order_params(side, pos_side, quantity, order_type, price, None, None)
                data = self._signed_request("POST", path, retry_plain, timeout=15)

            # Nếu thiếu margin, giảm dần khối lượng và thử lại.
            cur_qty = float(quantity)
            while self._extract_code(data) == 101204 and cur_qty > 0.001:
                cur_qty = round(cur_qty / 2, 4)
                retry_params = self._build_entry_order_params(side, pos_side, cur_qty, order_type, price, None, None)
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

    def close_position_market(self, pos_side, quantity=None):
        """
        Đóng vị thế theo market cho đúng chiều positionSide.
        Trả về response API để caller tự xử lý thành công/thất bại.
        """
        try:
            close_side = "SELL" if pos_side == "LONG" else "BUY"
            qty = quantity
            if qty is None or float(qty) <= 0:
                cur = self.get_open_position()
                if not cur or cur.get("side") != pos_side:
                    return {"code": -1, "msg": "Không tìm thấy vị thế phù hợp để đóng"}
                qty = cur.get("quantity", 0)

            params = {
                "symbol": SYMBOL,
                "side": close_side,
                "positionSide": pos_side,
                "type": "MARKET",
                "quantity": round(float(qty), 4),
                "timestamp": int(time.time() * 1000),
                "recvWindow": 5000
            }
            return self._signed_request("POST", "/openApi/swap/v2/trade/order", params, timeout=15)
        except Exception as e:
            print(f"[WARN] close_position_market exception: {e}")
            return {"code": -1, "msg": str(e)}

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
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi'] = (100 - (100 / (1 + rs))).fillna(50)
    tr = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            (df['high'] - df['close'].shift(1)).abs(),
            (df['low'] - df['close'].shift(1)).abs()
        )
    )
    df['atr'] = tr.ewm(span=14, adjust=False).mean()
    df['atr_pct'] = (df['atr'] / df['close']) * 100
    return df

def _swing_highs(df, n=3):
    idx = []
    for i in range(n, len(df)-n):
        if all(df['high'].iloc[i] > df['high'].iloc[i-j] for j in range(1, n+1)) and \
           all(df['high'].iloc[i] > df['high'].iloc[i+j] for j in range(1, n+1)):
            idx.append(i)
    return idx

def _swing_lows(df, n=3):
    idx = []
    for i in range(n, len(df)-n):
        if all(df['low'].iloc[i] < df['low'].iloc[i-j] for j in range(1, n+1)) and \
           all(df['low'].iloc[i] < df['low'].iloc[i+j] for j in range(1, n+1)):
            idx.append(i)
    return idx

def scan_signal_backtest_v5(df):
    if len(df) < 260:
        return None
    i = len(df) - 2
    sh = _swing_highs(df, n=3)
    sl = _swing_lows(df, n=3)
    prev_sh = [h for h in sh if h < i-1]
    prev_sl = [l for l in sl if l < i-1]
    if not prev_sh or not prev_sl:
        return None

    c = float(df['close'].iloc[i])
    sig = None
    if c > float(df['high'].iloc[max(prev_sh)]):
        sig = "BULL"
    elif c < float(df['low'].iloc[max(prev_sl)]):
        sig = "BEAR"
    if not sig:
        return None

    atr = float(df['atr'].iloc[i])
    ob = None
    start = max(0, i - 20)
    if sig == "BULL":
        for j in range(i-1, start-1, -1):
            o, c2 = float(df['open'].iloc[j]), float(df['close'].iloc[j])
            h, l = float(df['high'].iloc[j]), float(df['low'].iloc[j])
            body = abs(c2-o); rng = h-l
            if c2 < o and rng > 0 and (body/rng) > 0.35 and body > atr*0.25:
                if not any(float(df['close'].iloc[k]) < l for k in range(j+1, i)):
                    ob = {"type": "BULL_OB", "hi": h, "lo": l, "mid": (h+l)/2}
                    break
    else:
        for j in range(i-1, start-1, -1):
            o, c2 = float(df['open'].iloc[j]), float(df['close'].iloc[j])
            h, l = float(df['high'].iloc[j]), float(df['low'].iloc[j])
            body = abs(c2-o); rng = h-l
            if c2 > o and rng > 0 and (body/rng) > 0.35 and body > atr*0.25:
                if not any(float(df['close'].iloc[k]) > h for k in range(j+1, i)):
                    ob = {"type": "BEAR_OB", "hi": h, "lo": l, "mid": (h+l)/2}
                    break
    if not ob:
        return None

    o = float(df['open'].iloc[i]); h = float(df['high'].iloc[i]); l = float(df['low'].iloc[i]); c = float(df['close'].iloc[i])
    po = float(df['open'].iloc[i-1]); pc = float(df['close'].iloc[i-1])
    rsi = float(df['rsi'].iloc[i]); ema200 = float(df['ema200'].iloc[i])
    if ob['type'] == "BULL_OB":
        body = c-o; rng = h-l
        confirm = (pc < po and c > o and o <= pc and c >= po) or (c > o and rng > 0 and (body/rng) > 0.55 and c > l + rng*0.6)
        valid = c >= ema200 and 35 <= rsi <= 70 and l <= ob["hi"]
        if not (confirm and valid):
            return None
        entry = ob["mid"]; sl = ob["lo"] - atr*0.5; risk = entry - sl
        if risk <= 0 or risk > atr*3:
            return None
        tp = entry + risk*RR
        return {'side': 'LONG', 'entry': round(entry,2), 'sl': round(sl,2), 'tp': round(tp,2), 'rr': RR, 'signal_mode': 'backtest_v5', 'source': 'BINGX', 'candle_time': str(df['datetime'].iloc[i])}
    else:
        body = o-c; rng = h-l
        confirm = (pc > po and c < o and o >= pc and c <= po) or (c < o and rng > 0 and (body/rng) > 0.55 and c < h - rng*0.6)
        valid = c <= ema200 and 30 <= rsi <= 65 and h >= ob["lo"]
        if not (confirm and valid):
            return None
        entry = ob["mid"]; sl = ob["hi"] + atr*0.5; risk = sl - entry
        if risk <= 0 or risk > atr*3:
            return None
        tp = entry - risk*RR
        return {'side': 'SHORT', 'entry': round(entry,2), 'sl': round(sl,2), 'tp': round(tp,2), 'rr': RR, 'signal_mode': 'backtest_v5', 'source': 'BINGX', 'candle_time': str(df['datetime'].iloc[i])}

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
    if resolve_signal_engine() == "backtest_v5":
        return scan_signal_backtest_v5(df)
    """
    Tín hiệu scalp SMC được siết chặt:
    1) Có bias rõ ràng theo EMA50/EMA200 + cấu trúc gần nhất.
    2) Có sweep thanh khoản (quét đỉnh/đáy swing gần nhất).
    3) Có MSS (close phá cấu trúc ngược lại sau sweep).
    4) ATR tối thiểu để tránh vùng nhiễu quá thấp.
    """
    min_bars = max(160, SWING_LOOKBACK + TREND_LOOKBACK + 5)
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

    long_strict = bullish_bias and liquidity_sweep_low and mss_bull and in_discount
    short_strict = bearish_bias and liquidity_sweep_high and mss_bear and in_premium

    # SMC-lite: vẫn giữ bias + MSS, nới điều kiện sweep bằng cách chấp nhận discount/premium zone.
    long_smc_lite = bullish_bias and mss_bull and (liquidity_sweep_low or in_discount)
    short_smc_lite = bearish_bias and mss_bear and (liquidity_sweep_high or in_premium)

    # Fallback mềm hơn: vẫn cùng xu hướng EMA + MSS nhưng không bắt buộc sweep, để tránh bỏ lỡ toàn bộ tín hiệu.
    near_ema50 = abs(close_price - ema50) <= max(0.5, close_price * 0.0035)
    long_fallback = ALLOW_FALLBACK_SIGNAL and bullish_bias and mss_bull and near_ema50
    short_fallback = ALLOW_FALLBACK_SIGNAL and bearish_bias and mss_bear and near_ema50

    if long_strict or long_smc_lite or long_fallback:
        e = round(close_price, 2)
        tp, sl, rr_used = calc_scalp_tp_sl(df, "LONG", e)
        if tp is None or sl is None:
            return None
        if long_strict:
            mode = "strict"
        elif long_smc_lite:
            mode = "smc_lite"
        else:
            mode = "fallback"
        return {
            'side': 'LONG',
            'entry': e,
            'sl': sl,
            'tp': tp,
            'rr': rr_used,
            'signal_mode': mode,
            'source': 'BINGX',
            'candle_time': str(last_closed['datetime'])
        }

    if short_strict or short_smc_lite or short_fallback:
        e = round(close_price, 2)
        tp, sl, rr_used = calc_scalp_tp_sl(df, "SHORT", e)
        if tp is None or sl is None:
            return None
        if short_strict:
            mode = "strict"
        elif short_smc_lite:
            mode = "smc_lite"
        else:
            mode = "fallback"
        return {
            'side': 'SHORT',
            'entry': e,
            'sl': sl,
            'tp': tp,
            'rr': rr_used,
            'signal_mode': mode,
            'source': 'BINGX',
            'candle_time': str(last_closed['datetime'])
        }

    # Lưới an toàn cuối: nếu engine strict/fallback chưa ra tín hiệu thì thử logic backtest_v5
    # để hạn chế tình trạng "đứng im" quá lâu khi thị trường đi một chiều mạnh.
    if ALLOW_FALLBACK_SIGNAL:
        backup_signal = scan_signal_backtest_v5(df)
        if backup_signal:
            backup_signal = dict(backup_signal)
            backup_signal["signal_mode"] = "backtest_v5_fallback"
            return backup_signal

    return None

def get_vst_balance_text():
    if not has_api_credentials():
        return "N/A (no API key)"
    return f"{bing_client.get_vst_balance():.4f} VST"

def format_startup_msg(vst_balance):
    mode_text = "READ-ONLY (chỉ gửi tín hiệu)" if not is_trading_enabled() else "TRADE TỰ ĐỘNG"
    engine_used = resolve_signal_engine()
    return (
        "🚀 <b>SMC Bot đã khởi động</b>\n"
        f"💵 Số dư: <b>{vst_balance:.4f} VST</b>\n"
        f"🧭 Chế độ: <b>{mode_text}</b>\n"
        f"🧠 Signal engine: <b>{engine_used}</b> (config={SIGNAL_ENGINE})\n"
        f"🕒 Thời gian: <b>{now_vn().strftime('%d/%m/%Y %H:%M')} (GMT+7)</b>"
    )

def format_signal_msg(signal, order_label=None):
    emoji = "🟢" if signal["side"] == "LONG" else "🔴"
    side_text = "MUA (LONG)" if signal["side"] == "LONG" else "BÁN (SHORT)"
    rr_text = format_rr_text(
        signal["side"], signal.get("entry"), signal.get("tp"), signal.get("sl"),
        fallback_rr=signal.get("rr", SCALP_RR_TARGET), decimals=1
    )
    signal_mode = signal.get("signal_mode", "strict")
    order_line = f"🆔 Mã lệnh  : <b>{order_label}</b>\n" if order_label else ""
    signal_source = signal.get("source", DATA_SOURCE)
    return (
        f"{emoji} <b>TÍN HIỆU SMC - {SYMBOL} {INTERVAL}</b>\n\n"
        f"{order_line}"
        f"📌 Lệnh      : <b>{side_text}</b>\n"
        f"💰 Giá hiện tại : <b>{format_price(signal['entry'])}</b>\n"
        f"🎯 Vào lệnh  : <b>{format_price(signal['entry'])}</b>\n"
        f"🛑 Cắt lỗ    : <b>{format_price(signal['sl'])}</b>\n"
        f"✅ Chốt lời  : <b>{format_price(signal['tp'])}</b>\n"
        f"📊 R:R       : <b>{rr_text}</b>\n"
        f"🧠 Mode      : <b>{signal_mode}</b>\n\n"
        f"💵 Số dư VST : <b>{get_vst_balance_text()}</b>\n"
        f"🔌 Nguồn dữ liệu: <b>{signal_source}</b>\n"
        f"⏰ <b>{format_vn_time(signal['candle_time'])} (GMT+7)</b>\n"
        "⚠️ <i>Chỉ tham khảo, tự xác nhận trước khi vào lệnh</i>"
    )

def format_status_msg(last_price, candle_time):
    next_time = now_vn() + timedelta(hours=1)
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
    rr_text = format_rr_text(
        signal["side"], entry_to_show, signal.get("tp"), signal.get("sl"),
        fallback_rr=signal.get("rr"), decimals=2
    )
    order_line = f"🆔 Mã lệnh  : <b>{order_label}</b>\n" if order_label else ""
    return (
        "🟢 <b>DEMO - Đặt lệnh thị trường</b>\n\n"
        f"{order_line}"
        f"📌 Lệnh     : <b>{'MUA (LONG)' if signal['side'] == 'LONG' else 'BÁN (SHORT)'}</b>\n"
        f"🎯 Entry    : <b>{format_price(entry_to_show)}</b>\n"
        f"🛑 Cắt lỗ   : <b>{format_price(signal['sl'])}</b>\n"
        f"✅ Chốt lời : <b>{format_price(signal['tp'])}</b>\n"
        f"📊 R:R      : <b>{rr_text}</b>\n"
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
    rr_text = format_rr_text(
        side, entry, position.get("tp"), position.get("sl"),
        fallback_rr=position.get("rr"), decimals=2
    )
    order_label = position.get("label", "LỆNH")
    return (
        f"{pnl_emoji} <b>Theo dõi lệnh mỗi 1 phút</b>\n\n"
        f"🆔 Mã lệnh  : <b>{order_label}</b>\n"
        f"📌 Lệnh      : <b>{'MUA (LONG)' if side == 'LONG' else 'BÁN (SHORT)'}</b>\n"
        f"🎯 Entry     : <b>{format_price(entry)}</b>\n"
        f"🛑 Cắt lỗ    : <b>{sl_text}</b>\n"
        f"✅ Chốt lời  : <b>{tp_text}</b>\n"
        f"📊 R:R       : <b>{rr_text}</b>\n"
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

vst_bal = bing_client.get_vst_balance() if has_api_credentials() else 0.0
existing_position = bing_client.get_open_position() if is_trading_enabled() else None
active_positions = []
order_seq = 0
if existing_position:
    order_seq += 1
    existing_position["label"] = f"LỆNH #{order_seq}"
    active_positions.append(existing_position)
if is_trading_enabled() and (not active_positions):
    bing_client.set_leverage("LONG", LEVERAGE)
    bing_client.set_leverage("SHORT", LEVERAGE)
send_telegram(format_startup_msg(vst_bal))
if not is_trading_enabled():
    send_telegram(
        "ℹ️ <b>Bot đang chạy ở chế độ READ-ONLY</b>\n"
        "Sẽ phân tích và gửi tín hiệu, nhưng không tự động đặt/đóng lệnh.\n"
        "Để bật auto trade: cung cấp BINGX_API_KEY + BINGX_SECRET_KEY và tắt READ_ONLY_MODE."
    )

last_signal_key = None
last_status_notify_ts = time.time()
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
            if signal.get("source", DATA_SOURCE) != "BINGX":
                print(f"[WARN] Bỏ qua tín hiệu do nguồn không phải BingX: {signal.get('source')}")
                time.sleep(10)
                continue
            sig_key = f"{signal['side']}_{signal['candle_time']}"
            # Khi restart bot: ghi nhận tín hiệu hiện tại, chỉ trade từ tín hiệu mới tiếp theo.
            if not bootstrapped_signal:
                if not is_trading_enabled():
                    # Ở chế độ chỉ cảnh báo thì không cần bỏ qua tín hiệu đầu tiên sau restart.
                    bootstrapped_signal = True
                else:
                    last_signal_key = sig_key
                    bootstrapped_signal = True
                    print(f"[INFO] Bootstrapped signal: {sig_key} - chờ tín hiệu mới để vào lệnh.")
                    time.sleep(10)
                    continue
            if sig_key != last_signal_key:
                if not is_trading_enabled():
                    order_seq += 1
                    order_label = f"LỆNH #{order_seq}"
                    send_telegram(format_signal_msg(signal, order_label))
                    send_telegram(
                        "🧪 <b>Bỏ qua đặt lệnh tự động</b>\n"
                        "Lý do: bot đang ở chế độ READ-ONLY."
                    )
                    last_signal_key = sig_key
                    time.sleep(10)
                    continue
                removable_positions = decide_positions_to_close(active_positions, signal["side"], float(live_price))
                for pos in removable_positions:
                    pnl_snapshot = calc_live_pnl(pos, float(live_price))
                    close_resp = bing_client.close_position_market(pos.get("side"), pos.get("quantity"))
                    close_ok = close_resp and close_resp.get("code") == 0
                    if close_ok:
                        active_positions = [x for x in active_positions if x.get("label") != pos.get("label")]
                    send_telegram(
                        "🔄 <b>Điều chỉnh danh mục lệnh</b>\n"
                        f"Đóng theo phân tích: <b>{pos.get('label')}</b> ({pos.get('side')})\n"
                        f"Kết quả đóng lệnh: <b>{'Thành công' if close_ok else 'Thất bại'}</b>\n"
                        f"PnL tạm tính khi đóng: <b>{pnl_snapshot:+.2f} USDT</b>\n"
                        f"Lý do: ưu tiên tín hiệu mới {signal['side']} và giữ tối đa {MAX_ACTIVE_ORDERS} lệnh.\n"
                        f"Chi tiết API: <b>{(close_resp or {}).get('msg', 'N/A')}</b>"
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
                rr_aligned_tp, rr_aligned_sl, rr_changed = align_tp_sl_with_rr(
                    order_signal["side"], last_price, order_signal.get("tp"), order_signal.get("sl"), signal.get("rr")
                )
                order_signal["tp"], order_signal["sl"] = rr_aligned_tp, rr_aligned_sl
                levels_changed = levels_changed or rr_changed
                pre_safe_tp, pre_safe_sl = order_signal["tp"], order_signal["sl"]
                order_signal["tp"], order_signal["sl"] = enforce_tp_sl_safety(
                    order_signal["side"], last_price, order_signal["tp"], order_signal["sl"], last_price
                )
                # enforce_tp_sl_safety có thể dịch TP/SL để hợp lệ với giá thị trường hiện tại,
                # nên đồng bộ lại RR một lần nữa để TP/SL vẫn bám RR mục tiêu.
                rr_final_tp, rr_final_sl, rr_final_changed = align_tp_sl_with_rr(
                    order_signal["side"], last_price, order_signal.get("tp"), order_signal.get("sl"), signal.get("rr")
                )
                order_signal["tp"], order_signal["sl"] = sanitize_tp_sl(
                    order_signal["side"], rr_final_tp, rr_final_sl, last_price
                )
                levels_changed = levels_changed or (
                    pre_safe_tp != order_signal["tp"] or pre_safe_sl != order_signal["sl"]
                )
                levels_changed = levels_changed or rr_final_changed
                effective_rr = calc_rr_from_levels(
                    order_signal["side"], last_price, order_signal.get("tp"), order_signal.get("sl")
                )
                if effective_rr is not None:
                    order_signal["rr"] = effective_rr
                if levels_changed:
                    send_telegram(
                        "🛠️ <b>Đã hiệu chỉnh TP/SL trước khi vào lệnh</b>\n"
                        f"TP: <b>{format_price(order_signal['tp'])}</b> | "
                        f"SL: <b>{format_price(order_signal['sl'])}</b>\n"
                        f"R:R thực tế theo entry: <b>1:{order_signal.get('rr', signal.get('rr', RR)):.2f}</b>\n"
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
        elif (not active_positions) and (time.time() - last_status_notify_ts >= 3600):
            send_telegram(format_status_msg(live_price, candle_time))
            last_status_notify_ts = time.time()

        # Nếu đã vào lệnh, gửi noti lời/lỗ mỗi 1 phút cho từng lệnh.
        if active_positions:
            now_ts = time.time()
            if now_ts - last_pnl_notify_ts >= 60:
                if is_trading_enabled():
                    exchange_pos = bing_client.get_open_position()
                    if not exchange_pos:
                        send_telegram("✅ <b>Không còn vị thế mở trên BingX</b>\nXóa danh sách lệnh đang theo dõi.")
                        active_positions = []
                        last_pnl_notify_ts = now_ts
                        time.sleep(10)
                        continue

                for pos in list(active_positions):
                    if not is_trading_enabled():
                        send_telegram(format_pnl_msg(pos, float(live_price)))
                        continue
                    if pos.get("tp") is not None:
                        if pos["side"] == "LONG" and float(live_price) >= float(pos["tp"]):
                            close_resp = bing_client.close_position_market(pos["side"], pos.get("quantity"))
                            send_telegram(f"🏁 <b>{pos.get('label')} đã chạm TP</b> | Đóng market: <b>{'OK' if (close_resp or {}).get('code') == 0 else 'Fail'}</b>")
                            active_positions.remove(pos)
                            continue
                        if pos["side"] == "SHORT" and float(live_price) <= float(pos["tp"]):
                            close_resp = bing_client.close_position_market(pos["side"], pos.get("quantity"))
                            send_telegram(f"🏁 <b>{pos.get('label')} đã chạm TP</b> | Đóng market: <b>{'OK' if (close_resp or {}).get('code') == 0 else 'Fail'}</b>")
                            active_positions.remove(pos)
                            continue
                    if pos.get("sl") is not None:
                        if pos["side"] == "LONG" and float(live_price) <= float(pos["sl"]):
                            close_resp = bing_client.close_position_market(pos["side"], pos.get("quantity"))
                            send_telegram(f"🛑 <b>{pos.get('label')} đã chạm SL</b> | Đóng market: <b>{'OK' if (close_resp or {}).get('code') == 0 else 'Fail'}</b>")
                            active_positions.remove(pos)
                            continue
                        if pos["side"] == "SHORT" and float(live_price) >= float(pos["sl"]):
                            close_resp = bing_client.close_position_market(pos["side"], pos.get("quantity"))
                            send_telegram(f"🛑 <b>{pos.get('label')} đã chạm SL</b> | Đóng market: <b>{'OK' if (close_resp or {}).get('code') == 0 else 'Fail'}</b>")
                            active_positions.remove(pos)
                            continue
                    send_telegram(format_pnl_msg(pos, float(live_price)))
                last_pnl_notify_ts = now_ts

        time.sleep(10)
    except Exception as e:
        print(f"Lỗi Main Loop: {e}")
        time.sleep(10)
