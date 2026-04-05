"""
bingx_client.py — API client giao tiếp với sàn BingX.
"""
import time
import json
import hmac
import hashlib

import pandas as pd

from config import (
    BINGX_API_KEY, BINGX_SECRET_KEY, BINGX_URL,
    HTTP_SESSION, HTTP_TIMEOUT, LEVERAGE, SYMBOL,
)
from utils import parse_trigger_price, pick_first_float, now_vn


def has_api_credentials():
    return bool(BINGX_API_KEY and BINGX_SECRET_KEY)


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
        headers = {
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded"
        }
        signed_query = self._build_signed_query(params)
        request_timeout = timeout or HTTP_TIMEOUT
        
        if method.upper() == "GET":
            r = HTTP_SESSION.get(f"{BINGX_URL}{path}?{signed_query}", headers=headers, timeout=request_timeout)
        else:
            r = HTTP_SESSION.post(f"{BINGX_URL}{path}?{signed_query}", headers=headers, timeout=request_timeout)
        return r.json()

    def _public_request(self, path, params=None, timeout=15):
        params = params or {}
        request_timeout = timeout or HTTP_TIMEOUT
        r = HTTP_SESSION.get(f"{BINGX_URL}{path}", params=params, timeout=request_timeout)
        return r.json()

    def get_balance_info(self, asset_name="VST"):
        """Lấy thông tin số dư chi tiết (balance/equity/availableMargin)."""
        if not has_api_credentials():
            return {"balance": 0.0, "equity": 0.0, "availableMargin": 0.0, "usedMargin": 0.0}
        
        path = "/openApi/swap/v2/user/balance"
        params = {"timestamp": int(time.time() * 1000), "recvWindow": 5000}
        try:
            data = self._signed_request("GET", path, params, timeout=10)
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

    def get_open_orders(self, symbol=None, order_type=None):
        """
        Theo tài liệu BingX: /openApi/swap/v2/trade/openOrders dùng để truy vấn lệnh chờ,
        bao gồm các lệnh TP/SL kiểu STOP_MARKET / TAKE_PROFIT_MARKET.
        """
        if not has_api_credentials():
            return []
            
        path = "/openApi/swap/v2/trade/openOrders"
        params = {"timestamp": int(time.time() * 1000), "recvWindow": 5000}
        if symbol:
            params["symbol"] = symbol
        if order_type:
            params["type"] = order_type
            
        try:
            data = self._signed_request("GET", path, params, timeout=10)
            if data.get("code") != 0:
                return []
            orders = data.get("data", [])
            if isinstance(orders, dict):
                orders = [orders]
            return orders if isinstance(orders, list) else []
        except Exception as e:
            print(f"[WARN] get_open_orders exception: {e}")
            return []

    def get_position_protection_levels(self, symbol, pos_side):
        """
        Lấy TP/SL từ openOrders theo type lệnh bảo vệ.
        """
        tp = None
        sl = None
        orders = self.get_open_orders(symbol=symbol)
        
        for order in orders:
            order_side = str(order.get("positionSide") or "").upper()
            if order_side and order_side != str(pos_side).upper():
                continue
                
            order_type = str(order.get("type") or "").upper()
            stop_price = parse_trigger_price(order.get("stopPrice"))
            if stop_price is None:
                stop_price = parse_trigger_price(order.get("price"))
            if stop_price is None:
                continue
                
            if ("TAKE_PROFIT" in order_type) and tp is None:
                tp = stop_price
            elif ("STOP" in order_type) and ("TAKE_PROFIT" not in order_type) and sl is None:
                sl = stop_price
                
            if tp is not None and sl is not None:
                break
                
        return tp, sl

    def get_open_position(self, symbol=None):
        """
        Lấy vị thế đang mở của SYMBOL để tránh lệnh trùng lặp hoặc đóng ngoài ý muốn.
        Mặc định sử dụng config SYMBOL nếu không cung cấp.
        """
        symbol = symbol or SYMBOL
        if not has_api_credentials():
            return None
            
        path = "/openApi/swap/v2/user/positions"
        params = {"symbol": symbol, "timestamp": int(time.time() * 1000), "recvWindow": 5000}
        
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
                    
                side = str(p.get("positionSide") or "").upper()
                if side not in {"LONG", "SHORT"}:
                    # Phỏng đoán side nếu như không trả về rõ positionSide 
                    side = "LONG" if qty > 0 else "SHORT"
                    
                entry = float(p.get("avgPrice", 0) or 0)
                tp, sl = self.get_position_protection_levels(symbol, side)
                
                return {
                    "side": side,
                    "entry": entry,
                    "quantity": abs(qty),
                    "tp": tp,
                    "sl": sl,
                    "opened_at": now_vn(),
                    "unrealizedProfit": float(p.get("unrealizedProfit", 0) or 0),
                    "positionValue": float(p.get("positionValue", 0) or 0),
                    "markPrice": float(p.get("markPrice", 0) or 0),
                    "leverage": float(p.get("leverage", LEVERAGE) or LEVERAGE),
                    "notional": abs(float(p.get("positionValue", 0) or 0)),
                    "margin": float(p.get("isolatedMargin", 0) or 0) or (abs(float(p.get("positionValue", 0) or 0)) / float(p.get("leverage", 100) or 100))
                }
        except Exception as e:
            print(f"[WARN] get_open_position exception: {e}")
            
        return None

    def get_last_price(self, symbol=None):
        """
        Lấy giá mới nhất trực tiếp từ BingX quote API.
        Thử lần lượt các endpoint để tương thích nhiều phiên bản API.
        """
        symbol = symbol or SYMBOL
        candidates = [
            "/openApi/swap/v2/quote/price",
            "/openApi/swap/v3/quote/price",
            "/openApi/swap/v1/ticker/price",
        ]
        for path in candidates:
            try:
                data = self._public_request(path, {"symbol": symbol}, timeout=10)
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

    def get_klines(self, symbol=None, interval="15m", limit=500):
        """
        Lấy nến trực tiếp từ BingX thay vì nguồn ngoài để đảm bảo khớp dữ liệu sàn.
        """
        symbol = symbol or SYMBOL
        endpoints = [
            "/openApi/swap/v3/quote/klines",
            "/openApi/swap/v2/quote/klines",
        ]
        
        for path in endpoints:
            try:
                data = self._public_request(path, {"symbol": symbol, "interval": interval, "limit": limit}, timeout=15)
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

    def set_leverage(self, symbol=None, side="LONG", leverage=None):
        symbol = symbol or SYMBOL
        leverage = leverage or LEVERAGE
        path = "/openApi/swap/v2/trade/leverage"
        params = {
            "symbol": symbol,
            "side": side,
            "leverage": int(leverage),
            "timestamp": int(time.time() * 1000),
            "recvWindow": 5000
        }
        try:
            data = self._signed_request("POST", path, params, timeout=10)
            print(f"[INFO] Set leverage {side} x{leverage} cho {symbol}: {data}")
            return data
        except Exception as e:
            print(f"[WARN] set_leverage {side} exception: {e}")
            return None

    def _build_entry_order_params(self, symbol, side, pos_side, quantity, order_type="MARKET", price=None, tp=None, sl=None):
        req = {
            "symbol": symbol,
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

    def place_order(self, symbol, side, pos_side, quantity, order_type="MARKET", price=None, tp=None, sl=None):
        path = "/openApi/swap/v2/trade/order"

        try:
            params = self._build_entry_order_params(symbol, side, pos_side, quantity, order_type, price, tp, sl)
            data = self._signed_request("POST", path, params, timeout=15)

            # Nếu lệnh đính kèm TP/SL bị từ chối, thử vào lệnh market trước rồi sẽ gắn TP/SL sau.
            if self._extract_code(data) != 0 and (tp is not None or sl is not None):
                print(f"[WARN] place_order with TP/SL failed, retry market only: {data}")
                retry_plain = self._build_entry_order_params(symbol, side, pos_side, quantity, order_type, price, None, None)
                data = self._signed_request("POST", path, retry_plain, timeout=15)

            # Nếu thiếu margin, giảm dần khối lượng và thử lại.
            cur_qty = float(quantity)
            while self._extract_code(data) == 101204 and cur_qty > 0.001:
                cur_qty = round(cur_qty / 2, 4)
                retry_params = self._build_entry_order_params(symbol, side, pos_side, cur_qty, order_type, price, None, None)
                print(f"[WARN] Insufficient margin, thử lại quantity={cur_qty}")
                data = self._signed_request("POST", path, retry_params, timeout=15)

            return data
        except Exception as e:
            print(f"[ERROR] place_order exception: {e}")
            return None

    def place_market_order(self, symbol, side, pos_side, quantity, tp=None, sl=None):
        return self.place_order(symbol, side, pos_side, quantity, "MARKET", None, tp, sl)

    def place_limit_order(self, symbol, side, pos_side, quantity, price, tp=None, sl=None):
        return self.place_order(symbol, side, pos_side, quantity, "LIMIT", price, tp, sl)

    def close_position_market(self, symbol, pos_side, quantity=None):
        """
        Đóng vị thế theo market cho đúng chiều positionSide.
        Trả về response API để caller tự xử lý thành công/thất bại.
        """
        try:
            close_side = "SELL" if pos_side == "LONG" else "BUY"
            qty = quantity
            if qty is None or float(qty) <= 0:
                cur = self.get_open_position(symbol)
                if not cur or cur.get("side") != pos_side:
                    return {"code": -1, "msg": "Không tìm thấy vị thế phù hợp để đóng"}
                qty = cur.get("quantity", 0)

            params = {
                "symbol": symbol,
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

    def add_missing_tp_sl(self, symbol, pos_side, tp=None, sl=None):
        """
        Nếu vị thế hiện tại chưa có TP/SL trên sàn thì đặt bổ sung ngay.
        """
        try:
            if tp is None and sl is None:
                return {"tp_added": False, "sl_added": False, "position": self.get_open_position(symbol)}

            position = self.get_open_position(symbol)
            if not position or position.get("side") != pos_side:
                return {"tp_added": False, "sl_added": False, "position": position}

            close_side = "SELL" if pos_side == "LONG" else "BUY"
            path = "/openApi/swap/v2/trade/order"
            result = {"tp_added": False, "sl_added": False, "position": position}

            if position.get("tp") is None and tp is not None:
                tp_params = {
                    "symbol": symbol,
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
                    "symbol": symbol,
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

            result["position"] = self.get_open_position(symbol)
            return result
        except Exception as e:
            print(f"[WARN] add_missing_tp_sl exception: {e}")
            return {"tp_added": False, "sl_added": False, "position": self.get_open_position(symbol)}

# Khởi tạo singleton dùng chung
bing_client = BingXClient(BINGX_API_KEY, BINGX_SECRET_KEY)
