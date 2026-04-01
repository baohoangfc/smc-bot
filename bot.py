import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
import numpy as np
import time
import os
import json
import hmac
import hashlib
import math
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
SYMBOLS_RAW      = os.environ.get("BINGX_SYMBOLS", "NCCOGOLD2USD-USDT,BTC-USDT")
SYMBOLS          = [s.strip() for s in SYMBOLS_RAW.split(",") if s.strip()]
if not SYMBOLS:
    SYMBOLS = [os.environ.get("BINGX_SYMBOL", "NCCOGOLD2USD-USDT")]
SYMBOL = SYMBOLS[0]
INTERVAL         = os.environ.get("INTERVAL", "15m")
SCALP_INTERVALS_RAW = os.environ.get("SCALP_INTERVALS", "5m,15m,1h")
SWING_INTERVALS_RAW = os.environ.get("SWING_INTERVALS", "4h")
RR               = float(os.environ.get("RR", "2.0"))
ORDER_NOTIONAL_USDT = float(os.environ.get("ORDER_NOTIONAL_USDT", "1000"))
LEVERAGE = int(os.environ.get("LEVERAGE", "100"))
MAX_ACTIVE_ORDERS = int(os.environ.get("MAX_ACTIVE_ORDERS", "3"))
MIN_TP_PCT = float(os.environ.get("MIN_TP_PCT", "0.20"))
MIN_SL_PCT = float(os.environ.get("MIN_SL_PCT", "0.20"))
SCALP_RR_TARGET = float(os.environ.get("SCALP_RR_TARGET", "1.4"))
SCALP_RR_MIN = float(os.environ.get("SCALP_RR_MIN", "1.0"))
SCALP_RR_MAX = float(os.environ.get("SCALP_RR_MAX", "1.8"))
SCALP_RR_MAX_HIGH_QUALITY = float(os.environ.get("SCALP_RR_MAX_HIGH_QUALITY", "2.20"))
SCALP_RR_MAX_MED_QUALITY = float(os.environ.get("SCALP_RR_MAX_MED_QUALITY", "1.80"))
QUALITY_HIGH_THRESHOLD = float(os.environ.get("QUALITY_HIGH_THRESHOLD", "2.50"))
QUALITY_MED_THRESHOLD = float(os.environ.get("QUALITY_MED_THRESHOLD", "2.00"))
SCALP_MIN_QUALITY_SCORE = float(os.environ.get("SCALP_MIN_QUALITY_SCORE", "1.8"))
MIN_SIGNAL_QUALITY_SCORE = float(os.environ.get("MIN_SIGNAL_QUALITY_SCORE", "2.00"))
SL_BUFFER_PCT = float(os.environ.get("SL_BUFFER_PCT", "0.12"))
SWING_LOOKBACK = int(os.environ.get("SWING_LOOKBACK", "6"))
MIN_RISK_PCT = float(os.environ.get("MIN_RISK_PCT", "0.15"))
MIN_ATR_PCT = float(os.environ.get("MIN_ATR_PCT", "0.03"))
TREND_LOOKBACK = int(os.environ.get("TREND_LOOKBACK", "20"))
ALLOW_FALLBACK_SIGNAL = os.environ.get("ALLOW_FALLBACK_SIGNAL", "true").lower() == "true"
READ_ONLY_MODE = os.environ.get("READ_ONLY_MODE", "false").lower() == "true"
SIGNAL_ENGINE = os.environ.get("SIGNAL_ENGINE", "auto").lower()  # auto | strict | backtest_v5
SWING_RR_TARGET = float(os.environ.get("SWING_RR_TARGET", "2.4"))
MIN_ORDER_RR = float(os.environ.get("MIN_ORDER_RR", "1.0"))
SIGNAL_COOLDOWN_SECONDS = int(os.environ.get("SIGNAL_COOLDOWN_SECONDS", "90"))
HIGH_QUALITY_THRESHOLD = float(os.environ.get("HIGH_QUALITY_THRESHOLD", "2.60"))
HIGH_QUALITY_COOLDOWN_FACTOR = float(os.environ.get("HIGH_QUALITY_COOLDOWN_FACTOR", "0.70"))
LEARNING_ENABLED = os.environ.get("LEARNING_ENABLED", "true").lower() == "true"
LEARNING_FILE = os.environ.get("LEARNING_FILE", "learning_state.json")
LEARNING_MIN_TRADES = int(os.environ.get("LEARNING_MIN_TRADES", "5"))
LEARNING_SAVE_INTERVAL = int(os.environ.get("LEARNING_SAVE_INTERVAL", "60"))
LIQUIDITY_FOCUS_ENABLED = os.environ.get("LIQUIDITY_FOCUS_ENABLED", "true").lower() == "true"
LIQUIDITY_FOCUS_MODE = os.environ.get("LIQUIDITY_FOCUS_MODE", "soft").lower()  # soft | strict
LIQUIDITY_WINDOWS_VN_RAW = os.environ.get("LIQUIDITY_WINDOWS_VN", "14-17,19-23")
LIQUIDITY_SOFT_MIN_RR = float(os.environ.get("LIQUIDITY_SOFT_MIN_RR", "1.30"))
LIQUIDITY_SOFT_MIN_QUALITY = float(os.environ.get("LIQUIDITY_SOFT_MIN_QUALITY", "2.20"))
HIGH_LIQUIDITY_MAX_ACTIVE_ORDERS = int(os.environ.get("HIGH_LIQUIDITY_MAX_ACTIVE_ORDERS", str(MAX_ACTIVE_ORDERS + 1)))
LOW_LIQUIDITY_MAX_ACTIVE_ORDERS = int(os.environ.get("LOW_LIQUIDITY_MAX_ACTIVE_ORDERS", str(max(1, MAX_ACTIVE_ORDERS - 1))))
ENTRY_DRIFT_MAX_PCT = float(os.environ.get("ENTRY_DRIFT_MAX_PCT", "0.30"))
FALLBACK_MIN_QUALITY_SCORE = float(os.environ.get("FALLBACK_MIN_QUALITY_SCORE", "2.50"))
FALLBACK_REQUIRE_HIGH_LIQUIDITY = os.environ.get("FALLBACK_REQUIRE_HIGH_LIQUIDITY", "true").lower() == "true"
BE_TRIGGER_PCT = float(os.environ.get("BE_TRIGGER_PCT", "50.0"))
BE_OFFSET_PCT = float(os.environ.get("BE_OFFSET_PCT", "0.02"))
TELEGRAM_DEDUP_WINDOW_SECONDS = float(os.environ.get("TELEGRAM_DEDUP_WINDOW_SECONDS", "20"))
GRID_BOT_ENABLED = os.environ.get("GRID_BOT_ENABLED", "true").lower() == "true"
GRID_INTERVAL = os.environ.get("GRID_INTERVAL", "1m").lower()
GRID_ANCHOR_WINDOW = int(os.environ.get("GRID_ANCHOR_WINDOW", "34"))
GRID_LEVELS = int(os.environ.get("GRID_LEVELS", "5"))
GRID_STEP_PCT = float(os.environ.get("GRID_STEP_PCT", "0.18"))
GRID_TP_FACTOR = float(os.environ.get("GRID_TP_FACTOR", "0.90"))
GRID_SL_FACTOR = float(os.environ.get("GRID_SL_FACTOR", "1.50"))
GRID_MIN_QUALITY_SCORE = float(os.environ.get("GRID_MIN_QUALITY_SCORE", "2.10"))
GRID_MIN_ATR_PCT = float(os.environ.get("GRID_MIN_ATR_PCT", "0.03"))
GRID_MIN_CANDLES = max(20, GRID_ANCHOR_WINDOW + 2)
WAIT_LOG_INTERVAL_SECONDS = int(os.environ.get("WAIT_LOG_INTERVAL_SECONDS", "60"))

def parse_intervals(raw_value, fallback):
    intervals = [s.strip().lower() for s in (raw_value or "").split(",") if s.strip()]
    return intervals or fallback

def parse_hour_windows(raw_value):
    windows = []
    for chunk in (raw_value or "").split(","):
        item = chunk.strip()
        if not item:
            continue
        if "-" in item:
            start_raw, end_raw = item.split("-", 1)
        else:
            start_raw, end_raw = item, item
        try:
            start_h = int(start_raw.strip())
            end_h = int(end_raw.strip())
            if 0 <= start_h <= 23 and 0 <= end_h <= 23:
                windows.append((start_h, end_h))
        except Exception:
            continue
    return windows

VALID_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"}

HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "15"))

def build_http_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.3,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

HTTP_SESSION = build_http_session()
_telegram_recent_messages = {}
_telegram_dedup_lock = threading.Lock()

def sanitize_intervals(intervals, fallback):
    valid = [i for i in intervals if i in VALID_INTERVALS]
    return valid or fallback

SCALP_INTERVALS = sanitize_intervals(parse_intervals(SCALP_INTERVALS_RAW, [INTERVAL]), [INTERVAL])
SWING_INTERVALS = sanitize_intervals(parse_intervals(SWING_INTERVALS_RAW, ["4h"]), ["4h"])
SIGNAL_INTERVALS = list(dict.fromkeys(SCALP_INTERVALS + SWING_INTERVALS))
if GRID_BOT_ENABLED and GRID_INTERVAL in VALID_INTERVALS and GRID_INTERVAL not in SIGNAL_INTERVALS:
    SIGNAL_INTERVALS.append(GRID_INTERVAL)
LIQUIDITY_WINDOWS_VN = parse_hour_windows(LIQUIDITY_WINDOWS_VN_RAW)

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

def parse_trigger_price(raw_value):
    """
    Chuẩn hóa dữ liệu TP/SL trả về từ API BingX.
    API có thể trả về: số, chuỗi số, JSON string, hoặc dict chứa stopPrice/price.
    """
    if raw_value is None:
        return None
    def _decode_json_layers(value, max_depth=3):
        current = value
        for _ in range(max_depth):
            if not isinstance(current, str):
                break
            stripped = current.strip()
            if not stripped:
                return None
            try:
                direct = float(stripped)
                if direct > 0:
                    return direct
            except Exception:
                pass

            # Hỗ trợ cả chuỗi JSON bị quote thêm 1 lớp:
            # "\"{\\\"stopPrice\\\":\\\"67400\\\"}\""
            if stripped[0] in {"{", "[", "\""}:
                try:
                    current = json.loads(stripped)
                    continue
                except Exception:
                    return None
            break
        return current

    parsed_value = _decode_json_layers(raw_value)

    if isinstance(parsed_value, list):
        for item in parsed_value:
            found = parse_trigger_price(item)
            if found is not None:
                return found
        return None

    if isinstance(parsed_value, dict):
        direct = pick_first_float(
            parsed_value.get("stopPrice"),
            parsed_value.get("price"),
            parsed_value.get("triggerPrice"),
            parsed_value.get("avgPrice"),
            parsed_value.get("activatePrice"),
            parsed_value.get("triggerPx"),
            parsed_value.get("stopPx"),
            parsed_value.get("trigger"),
            parsed_value.get("value")
        )
        if direct is not None:
            return direct

        # Ưu tiên bóc các nhánh hay gặp trong payload BingX.
        nested_priority_keys = [
            "data", "order", "params", "detail", "extra",
            "takeProfit", "stopLoss", "tpOrder", "slOrder", "trigger"
        ]
        for key in nested_priority_keys:
            if key not in parsed_value:
                continue
            found = parse_trigger_price(parsed_value.get(key))
            if found is not None:
                return found

        # Fallback: quét toàn bộ dict nếu BingX đổi schema.
        for value in parsed_value.values():
            found = parse_trigger_price(value)
            if found is not None:
                return found
        return None

    return pick_first_float(parsed_value)

def _clamp(value, low, high):
    return max(low, min(high, value))

def load_learning_state():
    if not LEARNING_ENABLED:
        return {}
    if not os.path.exists(LEARNING_FILE):
        return {}
    try:
        with open(LEARNING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[WARN] load_learning_state failed: {e}")
        return {}

def save_learning_state(state):
    if not LEARNING_ENABLED:
        return
    try:
        with open(LEARNING_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] save_learning_state failed: {e}")

def mark_learning_dirty(meta):
    if LEARNING_ENABLED:
        meta["dirty"] = True

def maybe_flush_learning_state(state, meta, force=False):
    if not LEARNING_ENABLED:
        return
    now_ts = time.time()
    if not meta.get("dirty"):
        return
    last_save_ts = float(meta.get("last_save_ts", 0.0))
    if force or (now_ts - last_save_ts >= LEARNING_SAVE_INTERVAL):
        save_learning_state(state)
        meta["dirty"] = False
        meta["last_save_ts"] = now_ts

def learning_key(symbol, strategy, interval, side):
    return f"{symbol}|{strategy}|{interval}|{side}"

def update_learning_state(state, symbol, strategy, interval, side, pnl):
    key = learning_key(symbol, strategy, interval, side)
    row = state.get(key, {
        "symbol": symbol, "strategy": strategy, "interval": interval, "side": side,
        "trades": 0, "wins": 0, "losses": 0, "pnl_sum": 0.0, "avg_pnl": 0.0, "win_rate": 0.0
    })
    row["trades"] += 1
    if pnl >= 0:
        row["wins"] += 1
    else:
        row["losses"] += 1
    row["pnl_sum"] = float(row.get("pnl_sum", 0.0)) + float(pnl)
    row["avg_pnl"] = row["pnl_sum"] / max(row["trades"], 1)
    row["win_rate"] = float(row["wins"]) / max(row["trades"], 1)
    state[key] = row
    print(
        f"[LEARN] Update {key} | trades={row['trades']} | win_rate={row['win_rate']:.2f} | "
        f"avg_pnl={row['avg_pnl']:+.2f} | last_pnl={float(pnl):+.2f}"
    )
    return row

def apply_learning_to_signal_v2(state, symbol, signal):
    if not LEARNING_ENABLED or not signal:
        return signal
    strategy = signal.get("strategy", "scalp")
    interval = signal.get("interval", INTERVAL)
    side = signal.get("side")
    key = learning_key(symbol, strategy, interval, side)
    row = state.get(key)
    if not row or int(row.get("trades", 0)) < LEARNING_MIN_TRADES:
        return signal
    learned_signal = dict(signal)
    win_rate = float(row.get("win_rate", 0.0))
    avg_pnl = float(row.get("avg_pnl", 0.0))

    # Chuẩn hóa avg_pnl theo notional để learning có tác động tương xứng.
    norm_base = max(ORDER_NOTIONAL_USDT * 0.02, 1.0)
    norm_pnl = avg_pnl / norm_base
    quality_adjust = _clamp((win_rate - 0.5) * 0.8 + norm_pnl * 0.5, -0.6, 0.6)
    learned_signal["quality_score"] = round(float(learned_signal.get("quality_score", 2.0)) + quality_adjust, 2)
    rr_base = float(learned_signal.get("rr", RR) or RR)
    rr_multiplier = _clamp(1.0 + (win_rate - 0.5) * 0.3, 0.9, 1.12)
    rr_target = rr_base * rr_multiplier
    tp_new, sl_new, _ = align_tp_sl_with_rr(
        side, float(learned_signal.get("entry", 0) or 0), learned_signal.get("tp"), learned_signal.get("sl"), rr_target
    )
    learned_signal["tp"] = tp_new
    learned_signal["sl"] = sl_new
    learned_signal["rr"] = rr_target
    learned_signal["learning_note"] = (
        f"win_rate={win_rate:.2f}, avg_pnl={avg_pnl:+.2f}, norm_base={norm_base:.1f}, "
        f"quality_adj={quality_adjust:+.2f}, rr_mul={rr_multiplier:.2f}"
    )
    print(f"[LEARN v2] Apply {key} | {learned_signal['learning_note']}")
    return learned_signal

def get_dynamic_rr_max(quality_score):
    q = float(quality_score or 0)
    if q >= QUALITY_HIGH_THRESHOLD:
        return SCALP_RR_MAX_HIGH_QUALITY
    if q >= QUALITY_MED_THRESHOLD:
        return SCALP_RR_MAX_MED_QUALITY
    return max(SCALP_RR_MIN, SCALP_RR_MAX_MED_QUALITY - 0.3)

def is_entry_still_valid(signal, live_price, max_drift_pct=None):
    if max_drift_pct is None:
        max_drift_pct = ENTRY_DRIFT_MAX_PCT
    entry = float(signal.get("entry") or 0)
    if entry <= 0 or live_price <= 0:
        return True
    drift_pct = abs(live_price - entry) / entry * 100.0
    if drift_pct > max_drift_pct:
        print(
            f"[DRIFT] Entry={entry:.2f}, live={live_price:.2f}, "
            f"drift={drift_pct:.3f}% > max={max_drift_pct:.2f}% → BỎ QUA"
        )
        return False
    return True

def is_signal_tradeable(signal):
    """
    Lọc tín hiệu trước khi vào lệnh để tránh setup RR quá thấp hoặc thiếu level TP/SL.
    """
    if not signal:
        return False, "Tín hiệu rỗng"
    side = signal.get("side")
    entry = signal.get("entry")
    tp = signal.get("tp")
    sl = signal.get("sl")
    rr = calc_rr_from_levels(side, entry, tp, sl)
    if rr is None:
        rr = float(signal.get("rr", 0) or 0)
    if rr <= 0:
        return False, "RR không hợp lệ"
    if rr < MIN_ORDER_RR:
        return False, f"RR thấp ({rr:.2f} < {MIN_ORDER_RR:.2f})"
    if entry is None or tp is None or sl is None:
        return False, "Thiếu entry/TP/SL"
    return True, f"RR={rr:.2f}"

def format_vn_time(dt_value, fmt="%d/%m/%Y %H:%M"):
    dt = pd.to_datetime(dt_value)
    return dt.strftime(fmt)

def interval_to_minutes(interval):
    mapping = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
    return mapping.get(interval, 15)

def build_telegram_dedup_keys(msg):
    """
    Tạo nhiều fingerprint để chống trùng noti tốt hơn:
    - full text (mặc định)
    - phần thân cảnh báo theo dõi lệnh (để bắt trường hợp có/không có dòng tiêu đề 📌 SYMBOL)
    """
    raw_text = str(msg or "")
    keys = [hashlib.sha256(raw_text.encode("utf-8")).hexdigest()]

    tracked_marker = "Theo dõi lệnh: báo khi ROI"
    marker_idx = raw_text.find(tracked_marker)
    if marker_idx >= 0:
        normalized_text = raw_text[marker_idx:].strip()
        keys.append(hashlib.sha256(normalized_text.encode("utf-8")).hexdigest())
    return keys

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    now_ts = time.time()
    msg_keys = build_telegram_dedup_keys(msg)
    with _telegram_dedup_lock:
        # Dọn cache cũ để tránh tăng bộ nhớ khi bot chạy lâu.
        expired_keys = [k for k, ts in _telegram_recent_messages.items() if now_ts - ts > TELEGRAM_DEDUP_WINDOW_SECONDS]
        for key in expired_keys:
            _telegram_recent_messages.pop(key, None)

        for key in msg_keys:
            last_sent_ts = _telegram_recent_messages.get(key)
            if last_sent_ts is not None and (now_ts - last_sent_ts) <= TELEGRAM_DEDUP_WINDOW_SECONDS:
                print("[INFO] Skip duplicate Telegram message within dedup window.")
                return
        for key in msg_keys:
            _telegram_recent_messages[key] = now_ts

    try:
        HTTP_SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=HTTP_TIMEOUT
        )
    except Exception as e:
        print(f"[WARN] send_telegram exception: {e}")

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

    def get_open_orders(self, symbol=SYMBOL, order_type=None):
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
        Theo docs /user/positions không trả TP/SL.
        TP/SL được lấy từ openOrders theo type lệnh bảo vệ.
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

    def get_open_position(self, symbol=SYMBOL):
        """
        Lấy vị thế đang mở của SYMBOL để tránh vào lệnh trùng/đóng lệnh ngoài ý muốn khi restart bot.
        """
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
                    "leverage": float(p.get("leverage", LEVERAGE) or LEVERAGE)
                }
        except Exception as e:
            print(f"[WARN] get_open_position exception: {e}")
        return None

    def get_last_price(self, symbol=SYMBOL):
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

    def get_klines(self, symbol=SYMBOL, interval="15m", limit=500):
        """
        Lấy nến trực tiếp từ BingX thay vì nguồn ngoài để đảm bảo khớp dữ liệu sàn.
        """
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

    def set_leverage(self, symbol=SYMBOL, side="LONG", leverage=100):
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
            print(f"[INFO] Set leverage {side} x{leverage}: {data}")
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

bing_client = BingXClient(BINGX_API_KEY, BINGX_SECRET_KEY)

# ==========================================
# (Các hàm fetch_data, indicators, SMC Logic giữ nguyên)
# ==========================================
def fetch_data(symbol, interval="15m", candles=500):
    # Dữ liệu nến dùng cho tín hiệu chỉ lấy từ API BingX (không dùng Yahoo/nguồn khác).
    try:
        return bing_client.get_klines(symbol=symbol, interval=interval, limit=candles)
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

def calc_scalp_tp_sl_v2(df, side, entry, quality_score=2.0):
    """
    Tính TP/SL theo hướng scalp ngắn:
    - SL bám cấu trúc swing gần nhất + buffer nhỏ
    - TP theo RR mục tiêu (mặc định ~1.4R) và bị chặn trong [SCALP_RR_MIN, SCALP_RR_MAX]
    """
    if len(df) < max(SWING_LOOKBACK + 2, 10):
        return None, None, None

    recent = df.iloc[-(SWING_LOOKBACK + 2):-1]
    atr_val = float(df['atr'].iloc[-2]) if 'atr' in df.columns and not pd.isna(df['atr'].iloc[-2]) else 0
    entry_f = float(entry)
    buffer_by_pct = entry_f * (SL_BUFFER_PCT / 100.0)
    buffer = max(0.5, buffer_by_pct, atr_val * 0.25)
    min_risk = max(0.5, entry_f * (MIN_RISK_PCT / 100.0))

    # Điều chỉnh RR theo "độ mạnh xu hướng + biến động":
    # - Trend mạnh => ưu tiên RR lớn hơn để tối đa hóa lợi nhuận.
    # - Volatility cao => giảm RR nhẹ để hiện thực lợi nhuận sớm hơn.
    ema_gap_pct = 0.0
    if 'ema50' in df.columns and 'ema200' in df.columns:
        ema50 = float(df['ema50'].iloc[-2])
        ema200 = float(df['ema200'].iloc[-2])
        base = max(abs(entry_f), 1e-9)
        ema_gap_pct = abs(ema50 - ema200) / base * 100.0

    vol_factor = 1.0
    if atr_val > 0:
        atr_pct = (atr_val / max(abs(entry_f), 1e-9)) * 100.0
        if atr_pct >= 0.9:
            vol_factor = 0.92
        elif atr_pct <= 0.25:
            vol_factor = 1.05

    trend_factor = 1.0
    if ema_gap_pct >= 1.0:
        trend_factor = 1.12
    elif ema_gap_pct >= 0.5:
        trend_factor = 1.06
    elif ema_gap_pct <= 0.2:
        trend_factor = 0.95

    rr_used = SCALP_RR_TARGET * trend_factor * vol_factor
    rr_ceiling = get_dynamic_rr_max(quality_score)
    rr_used = min(max(rr_used, SCALP_RR_MIN), rr_ceiling)

    if side == "LONG":
        structure_sl = float(recent['low'].min()) - buffer
        risk = max(entry_f - structure_sl, min_risk)
        if atr_val > 0 and risk < atr_val * 0.5:
            risk = atr_val * 0.5
        sl = entry_f - risk
        tp = entry_f + (risk * rr_used)
    else:
        structure_sl = float(recent['high'].max()) + buffer
        risk = max(structure_sl - entry_f, min_risk)
        if atr_val > 0 and risk < atr_val * 0.5:
            risk = atr_val * 0.5
        sl = entry_f + risk
        tp = entry_f - (risk * rr_used)

    return round(tp, 2), round(sl, 2), rr_used

def _scan_signal_fallback_section(df):
    if not ALLOW_FALLBACK_SIGNAL:
        return None
    if FALLBACK_REQUIRE_HIGH_LIQUIDITY and not is_high_liquidity_time(now_vn()):
        print("[FALLBACK] Ngoài giờ thanh khoản cao → bỏ qua fallback signal")
        return None
    backup_signal = scan_signal_backtest_v5(df)
    if not backup_signal:
        return None
    quality = float(backup_signal.get("quality_score", 0) or 0)
    if quality < FALLBACK_MIN_QUALITY_SCORE:
        print(
            f"[FALLBACK] quality={quality:.2f} < threshold={FALLBACK_MIN_QUALITY_SCORE:.2f} → bỏ qua"
        )
        return None
    backup_signal = dict(backup_signal)
    backup_signal["signal_mode"] = "backtest_v5_fallback"
    return backup_signal

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

    # Chấm điểm chất lượng setup để lọc bớt lệnh xác suất thấp.
    trend_strength = abs(ema50 - ema200) / max(close_price, 1e-9) * 100.0
    quality_score = 0.0
    quality_score += 1.0 if bullish_bias or bearish_bias else 0.0
    quality_score += 1.0 if liquidity_sweep_low or liquidity_sweep_high else 0.0
    quality_score += 0.8 if mss_bull or mss_bear else 0.0
    quality_score += min(0.8, trend_strength * 0.8)
    quality_score += min(0.6, atr_pct * 0.25)

    long_strict = bullish_bias and liquidity_sweep_low and mss_bull and in_discount
    short_strict = bearish_bias and liquidity_sweep_high and mss_bear and in_premium

    # SMC-lite: vẫn giữ bias + MSS, nới điều kiện sweep bằng cách chấp nhận discount/premium zone.
    long_smc_lite = bullish_bias and mss_bull and (liquidity_sweep_low or in_discount)
    short_smc_lite = bearish_bias and mss_bear and (liquidity_sweep_high or in_premium)

    # Fallback mềm hơn: vẫn cùng xu hướng EMA + MSS nhưng không bắt buộc sweep, để tránh bỏ lỡ toàn bộ tín hiệu.
    near_ema50 = abs(close_price - ema50) <= max(0.5, close_price * 0.0035)
    long_fallback = ALLOW_FALLBACK_SIGNAL and bullish_bias and mss_bull and near_ema50
    short_fallback = ALLOW_FALLBACK_SIGNAL and bearish_bias and mss_bear and near_ema50

    allow_long = long_strict or (long_smc_lite and quality_score >= SCALP_MIN_QUALITY_SCORE) or (long_fallback and quality_score >= (SCALP_MIN_QUALITY_SCORE + 0.2))
    if allow_long:
        e = round(close_price, 2)
        tp, sl, rr_used = calc_scalp_tp_sl_v2(df, "LONG", e, quality_score=quality_score)
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
            'quality_score': round(quality_score, 2),
            'signal_mode': mode,
            'source': 'BINGX',
            'candle_time': str(last_closed['datetime'])
        }

    allow_short = short_strict or (short_smc_lite and quality_score >= SCALP_MIN_QUALITY_SCORE) or (short_fallback and quality_score >= (SCALP_MIN_QUALITY_SCORE + 0.2))
    if allow_short:
        e = round(close_price, 2)
        tp, sl, rr_used = calc_scalp_tp_sl_v2(df, "SHORT", e, quality_score=quality_score)
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
            'quality_score': round(quality_score, 2),
            'signal_mode': mode,
            'source': 'BINGX',
            'candle_time': str(last_closed['datetime'])
        }

    # Lưới an toàn cuối: nếu engine strict/fallback chưa ra tín hiệu thì thử logic backtest_v5
    # để hạn chế tình trạng "đứng im" quá lâu khi thị trường đi một chiều mạnh.
    return _scan_signal_fallback_section(df)

def scan_swing_signal(df):
    """
    Tín hiệu swing: tái dùng logic backtest_v5 và nâng RR mục tiêu cho lệnh giữ dài hơn.
    """
    base = scan_signal_backtest_v5(df)
    if not base:
        return None

    signal = dict(base)
    signal["strategy"] = "swing"
    signal["signal_mode"] = "swing_backtest_v5"

    entry = float(signal.get("entry", 0) or 0)
    sl = float(signal.get("sl", 0) or 0)
    if entry > 0 and sl > 0:
        if signal["side"] == "LONG":
            risk = entry - sl
            if risk > 0:
                rr_used = max(float(signal.get("rr", RR) or RR), SWING_RR_TARGET)
                signal["rr"] = rr_used
                signal["tp"] = round(entry + risk * rr_used, 2)
        else:
            risk = sl - entry
            if risk > 0:
                rr_used = max(float(signal.get("rr", RR) or RR), SWING_RR_TARGET)
                signal["rr"] = rr_used
                signal["tp"] = round(entry - risk * rr_used, 2)

    signal["quality_score"] = round(float(signal.get("quality_score", 2.1)), 2)
    return signal

def scan_grid_signal(df):
    """
    Grid fast scalp: vào lệnh ngược chiều khi giá lệch khỏi anchor ngắn hạn theo từng "nấc lưới".
    Mục tiêu là ăn nhịp hồi nhanh với TP ngắn và SL cố định theo bội số của bước lưới.
    """
    if not GRID_BOT_ENABLED or df is None or len(df) < GRID_MIN_CANDLES:
        return None

    latest = df.iloc[-2]
    entry = float(latest["close"])
    if entry <= 0:
        return None

    anchor_series = df["close"].rolling(window=max(5, GRID_ANCHOR_WINDOW)).mean()
    anchor = float(anchor_series.iloc[-2]) if not pd.isna(anchor_series.iloc[-2]) else 0.0
    if anchor <= 0:
        return None

    atr_value = float(latest.get("atr", 0) or 0)
    atr_pct = (atr_value / entry * 100) if atr_value > 0 else 0
    if atr_pct < GRID_MIN_ATR_PCT:
        return None

    deviation_pct = ((entry - anchor) / anchor) * 100.0
    level_size = max(0.02, GRID_STEP_PCT)
    level_idx = min(max(0, int(abs(deviation_pct) / level_size)), max(1, GRID_LEVELS))
    if level_idx < 1:
        return None

    side = "SHORT" if deviation_pct > 0 else "LONG"
    tp_distance_pct = max(MIN_TP_PCT, level_size * level_idx * max(0.5, GRID_TP_FACTOR))
    sl_distance_pct = max(MIN_SL_PCT, level_size * level_idx * max(1.0, GRID_SL_FACTOR))
    # Đảm bảo RR của grid không thấp hơn ngưỡng vào lệnh tối thiểu,
    # tránh tạo tín hiệu rồi bị loại ngay ở gate tradeability.
    min_tp_for_rr_pct = sl_distance_pct * max(0.5, MIN_ORDER_RR)
    tp_distance_pct = max(tp_distance_pct, min_tp_for_rr_pct)
    if side == "LONG":
        tp = round(entry * (1 + tp_distance_pct / 100.0), 2)
        sl = round(entry * (1 - sl_distance_pct / 100.0), 2)
    else:
        tp = round(entry * (1 - tp_distance_pct / 100.0), 2)
        sl = round(entry * (1 + sl_distance_pct / 100.0), 2)

    rr_now = calc_rr_from_levels(side, entry, tp, sl) or 0
    quality = 1.8 + min(1.2, level_idx * 0.22) + min(0.5, atr_pct * 0.15)
    if quality < GRID_MIN_QUALITY_SCORE:
        return None

    return {
        "side": side,
        "entry": round(entry, 2),
        "sl": sl,
        "tp": tp,
        "rr": round(rr_now, 2),
        "quality_score": round(quality, 2),
        "signal_mode": "grid_fast",
        "strategy": "grid",
        "source": "BINGX",
        "grid_level": level_idx,
        "grid_anchor": round(anchor, 2),
        "candle_time": str(latest["datetime"]),
    }

def signal_priority_score(signal, dt_value=None):
    quality = float(signal.get("quality_score", 0) or 0)
    rr_value = float(signal.get("rr", 0) or 0)
    strategy = signal.get("strategy", "scalp")
    # RR bị giới hạn trần để tránh score bị méo bởi outlier
    rr_component = min(max(rr_value, 0.0), 3.0)
    score = quality * 1.2 + rr_component * 0.8
    if is_high_liquidity_time(dt_value):
        score += 0.25 if strategy == "scalp" else 0.10
    else:
        score += 0.25 if strategy == "swing" else 0.05
    return round(score, 4)

def pick_best_signal(signal_candidates, dt_value=None):
    if not signal_candidates:
        return None
    # Ưu tiên điểm tổng hợp (quality + RR + chiến lược theo bối cảnh thanh khoản),
    # sau đó mới fallback theo quality và RR.
    return max(
        signal_candidates,
        key=lambda s: (
            signal_priority_score(s, dt_value),
            float(s.get("quality_score", 0) or 0),
            float(s.get("rr", 0) or 0),
            1 if s.get("strategy") == "swing" else 0,
        ),
    )

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
        f"📚 Danh mục: <b>{', '.join(SYMBOLS)}</b>\n"
        f"⏱️ Scalp TF: <b>{', '.join(SCALP_INTERVALS)}</b>\n"
        f"📈 Swing TF: <b>{', '.join(SWING_INTERVALS)}</b>\n"
        f"🧱 Grid fast: <b>{'ON' if GRID_BOT_ENABLED else 'OFF'}</b> ({GRID_INTERVAL}, step={GRID_STEP_PCT:.2f}%)\n"
        f"🧠 Signal engine: <b>{engine_used}</b> (config={SIGNAL_ENGINE})\n"
        f"🕒 Thời gian: <b>{now_vn().strftime('%d/%m/%Y %H:%M')} (GMT+7)</b>"
    )

def build_entry_reason(signal):
    strategy = signal.get("strategy", "scalp")
    tf = signal.get("interval", INTERVAL)
    mode = signal.get("signal_mode", "strict")
    quality_score = signal.get("quality_score")
    quality_text = f"{float(quality_score):.2f}" if quality_score is not None else "N/A"
    rr_text = format_rr_text(
        signal["side"], signal.get("entry"), signal.get("tp"), signal.get("sl"),
        fallback_rr=signal.get("rr", SCALP_RR_TARGET), decimals=2
    )
    return f"{strategy.upper()} {tf} | mode={mode} | quality={quality_text} | RR={rr_text}"

def format_signal_msg(signal, symbol, order_label=None):
    emoji = "🟢" if signal["side"] == "LONG" else "🔴"
    side_text = "MUA (LONG)" if signal["side"] == "LONG" else "BÁN (SHORT)"
    rr_text = format_rr_text(
        signal["side"], signal.get("entry"), signal.get("tp"), signal.get("sl"),
        fallback_rr=signal.get("rr", SCALP_RR_TARGET), decimals=1
    )
    signal_mode = signal.get("signal_mode", "strict")
    quality_score = signal.get("quality_score")
    quality_text = f"{float(quality_score):.2f}" if quality_score is not None else "N/A"
    tf = signal.get("interval", INTERVAL)
    strategy = signal.get("strategy", "scalp")
    order_line = f"🆔 Mã lệnh  : <b>{order_label}</b>\n" if order_label else ""
    signal_source = signal.get("source", DATA_SOURCE)
    entry_reason = build_entry_reason(signal)
    return (
        f"{emoji} <b>TÍN HIỆU SMC - {symbol} {tf}</b>\n\n"
        f"{order_line}"
        f"📌 Lệnh      : <b>{side_text}</b>\n"
        f"🧩 Chiến lược: <b>{strategy}</b>\n"
        f"💰 Giá hiện tại : <b>{format_price(signal['entry'])}</b>\n"
        f"🎯 Vào lệnh  : <b>{format_price(signal['entry'])}</b>\n"
        f"🛑 Cắt lỗ    : <b>{format_price(signal['sl'])}</b>\n"
        f"✅ Chốt lời  : <b>{format_price(signal['tp'])}</b>\n"
        f"📊 R:R       : <b>{rr_text}</b>\n"
        f"⭐ Quality   : <b>{quality_text}</b>\n"
        f"🧠 Mode      : <b>{signal_mode}</b>\n\n"
        f"📝 Lý do vào lệnh: <b>{entry_reason}</b>\n"
        f"💵 Số dư VST : <b>{get_vst_balance_text()}</b>\n"
        f"🔌 Nguồn dữ liệu: <b>{signal_source}</b>\n"
        f"⏰ <b>{format_vn_time(signal['candle_time'])} (GMT+7)</b>\n"
        "⚠️ <i>Chỉ tham khảo, tự xác nhận trước khi vào lệnh</i>"
    )

def format_status_msg(symbol, last_price, candle_time, wait_reason=None):
    next_time = now_vn() + timedelta(hours=1)
    reason_text = wait_reason or "Chưa có setup đạt điều kiện vào lệnh ở các khung đang theo dõi."
    return (
        f"🤖 <b>SMC Bot - Cập nhật {format_vn_time(candle_time, '%H:%M')} (GMT+7)</b>\n\n"
        f"Giá {symbol} : <b>{format_price(last_price)}</b>\n"
        f"Khung TG    : <b>{INTERVAL}</b>\n"
        f"Nguồn dữ liệu: <b>{DATA_SOURCE}</b>\n"
        f"Số dư VST   : <b>{get_vst_balance_text()}</b>\n"
        "Trạng thái  : ✅ <b>Đang chạy</b>\n\n"
        "⏳ Chưa có tín hiệu. Đang theo dõi...\n\n"
        f"📝 Lý do chờ: <b>{reason_text}</b>\n\n"
        f"Cập nhật tiếp theo lúc <b>{format_vn_time(next_time, '%H:%M')}</b>"
    )

def format_order_result_msg(signal, symbol, order_result, order_label=None, filled_entry=None):
    order_id = (order_result or {}).get("data", {}).get("order", {}).get("orderId", "N/A")
    entry_to_show = filled_entry if filled_entry is not None else signal.get("entry")
    rr_text = format_rr_text(
        signal["side"], entry_to_show, signal.get("tp"), signal.get("sl"),
        fallback_rr=signal.get("rr"), decimals=2
    )
    order_line = f"🆔 Mã lệnh  : <b>{order_label}</b>\n" if order_label else ""
    return (
        "🟢 <b>DEMO - Đặt lệnh thị trường</b>\n\n"
        f"🏷️ Mã        : <b>{symbol}</b>\n"
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
    qty = float(position.get("quantity", 0) or 0)
    entry = float(position.get("entry", 0) or 0)
    pnl = calc_live_pnl(position, last_price)
    if qty <= 0 or entry <= 0:
        pnl = float(position.get("unrealizedProfit", 0) or 0)
    notional_base = calc_position_notional_base(position)
    notional_pnl_pct = (pnl / notional_base) * 100 if notional_base else 0
    pnl_pct = calc_live_pnl_pct(position, last_price)
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    price_to_show = float(last_price)
    tp_text = format_price(position.get("tp")) if position.get("tp") is not None else "Chưa có"
    sl_text = format_price(position.get("sl")) if position.get("sl") is not None else "Chưa có"
    rr_text = format_rr_text(
        side, entry, position.get("tp"), position.get("sl"),
        fallback_rr=position.get("rr"), decimals=2
    )
    order_label = position.get("label", "LỆNH")
    return (
        f"{pnl_emoji} <b>Theo dõi lệnh: báo khi ROI (PnL% trên ký quỹ) biến động ±10% so với lần báo trước</b>\n\n"
        f"🆔 Mã lệnh  : <b>{order_label}</b>\n"
        f"📌 Lệnh      : <b>{'MUA (LONG)' if side == 'LONG' else 'BÁN (SHORT)'}</b>\n"
        f"🎯 Entry     : <b>{format_price(entry)}</b>\n"
        f"🛑 Cắt lỗ    : <b>{sl_text}</b>\n"
        f"✅ Chốt lời  : <b>{tp_text}</b>\n"
        f"📊 R:R       : <b>{rr_text}</b>\n"
        f"💰 Giá hiện tại: <b>{format_price(price_to_show)}</b>\n"
        f"📦 Khối lượng : <b>{qty}</b>\n"
        f"💵 PnL tạm tính: <b>{pnl:+.2f} USDT</b>\n"
        f"📈 ROI ký quỹ: <b>{pnl_pct:+.2f}%</b> | PnL/notional: <b>{notional_pnl_pct:+.2f}%</b>\n"
        f"⏰ <b>{now_vn().strftime('%d/%m/%Y %H:%M')} (GMT+7)</b>"
    )

def calc_live_pnl(position, last_price):
    side = position.get("side")
    qty = float(position.get("quantity", 0) or 0)
    entry = float(position.get("entry", 0) or 0)
    last_price = float(last_price or 0)
    if qty <= 0 or entry <= 0 or last_price <= 0:
        return float(position.get("unrealizedProfit", 0) or 0)
    if side == "LONG":
        return (last_price - entry) * qty
    return (entry - last_price) * qty

def calc_position_notional_base(position):
    qty = float(position.get("quantity", 0) or 0)
    entry = float(position.get("entry", 0) or 0)
    notional_entry = abs(entry * qty)
    api_position_value = float(position.get("positionValue", 0) or 0)
    return api_position_value if api_position_value > 0 else (notional_entry if notional_entry > 0 else ORDER_NOTIONAL_USDT)

def calc_live_pnl_pct(position, last_price):
    """
    PnL% dùng cho cảnh báo theo ROI trên ký quỹ (margin), không phải % trên notional.
    Ví dụ x100: PnL +4 USDT với notional 1000 => margin ~10 USDT => ROI ~+40%.
    """
    pnl = calc_live_pnl(position, last_price)
    notional_base = calc_position_notional_base(position)
    leverage = float(position.get("leverage", LEVERAGE) or LEVERAGE or 1)
    leverage = max(leverage, 1.0)
    margin_base = (notional_base / leverage) if notional_base else 0
    pnl_pct = (pnl / margin_base) * 100 if margin_base else 0
    return pnl_pct

def sync_position_levels_from_exchange(tracked_position, exchange_position):
    """
    Đồng bộ TP/SL và một số trường định lượng từ vị thế BingX sang position local.
    Giúp noti hiển thị đúng khi API trả TP/SL muộn hoặc dưới dạng JSON lồng.
    """
    if not tracked_position or not exchange_position:
        return tracked_position
    if tracked_position.get("side") != exchange_position.get("side"):
        return tracked_position

    updated = dict(tracked_position)
    for field in ("tp", "sl", "entry", "quantity", "positionValue", "leverage"):
        incoming = exchange_position.get(field)
        current = updated.get(field)
        if field in ("tp", "sl"):
            if current is None and incoming is not None:
                updated[field] = incoming
            continue
        if (current is None or float(current or 0) <= 0) and incoming is not None:
            updated[field] = incoming
    return updated

def check_breakeven_condition(pos, live_price, symbol=""):
    if pos.get("be_activated"):
        return pos

    entry = float(pos.get("entry") or 0)
    tp = float(pos.get("tp") or 0)
    sl = float(pos.get("sl") or 0)
    side = pos.get("side")
    if entry <= 0 or tp <= 0 or sl <= 0:
        return pos

    if side == "LONG":
        full_range = tp - entry
        if full_range <= 0:
            return pos
        progress_pct = (float(live_price) - entry) / full_range * 100.0
        if progress_pct >= BE_TRIGGER_PCT:
            new_sl = round(entry * (1 + BE_OFFSET_PCT / 100.0), 2)
            if new_sl > sl:
                pos = dict(pos)
                pos["sl"] = new_sl
                pos["be_activated"] = True
                print(
                    f"[BE] {symbol} {pos.get('label')} LONG: progress={progress_pct:.1f}% "
                    f"→ dịch SL lên BE {new_sl:.2f}"
                )
                send_telegram(
                    f"🔒 <b>{symbol} - {pos.get('label')}: Kích hoạt Breakeven</b>\n"
                    f"📈 Lệnh đang lãi {progress_pct:.1f}% tiến đến TP\n"
                    f"🛑 SL mới: <b>{new_sl:.2f}</b> (Breakeven +{BE_OFFSET_PCT:.2f}%)\n"
                    f"⏰ {now_vn().strftime('%d/%m %H:%M')} (GMT+7)"
                )
    else:
        full_range = entry - tp
        if full_range <= 0:
            return pos
        progress_pct = (entry - float(live_price)) / full_range * 100.0
        if progress_pct >= BE_TRIGGER_PCT:
            new_sl = round(entry * (1 - BE_OFFSET_PCT / 100.0), 2)
            if new_sl < sl:
                pos = dict(pos)
                pos["sl"] = new_sl
                pos["be_activated"] = True
                print(
                    f"[BE] {symbol} {pos.get('label')} SHORT: progress={progress_pct:.1f}% "
                    f"→ dịch SL xuống BE {new_sl:.2f}"
                )
                send_telegram(
                    f"🔒 <b>{symbol} - {pos.get('label')}: Kích hoạt Breakeven</b>\n"
                    f"📉 Lệnh đang lãi {progress_pct:.1f}% tiến đến TP\n"
                    f"🛑 SL mới: <b>{new_sl:.2f}</b> (Breakeven +{BE_OFFSET_PCT:.2f}%)\n"
                    f"⏰ {now_vn().strftime('%d/%m %H:%M')} (GMT+7)"
                )
    return pos

def should_notify_pnl_change(prev_notified_pct, current_pct, threshold=10.0):
    """
    Gửi thông báo khi PnL% thay đổi ít nhất `threshold` so với lần đã thông báo gần nhất.
    Hỗ trợ cả tăng và giảm (ví dụ +10% hoặc -10%).
    """
    if prev_notified_pct is None:
        return True
    return abs(float(current_pct) - float(prev_notified_pct)) >= float(threshold)

def is_in_hour_window(hour_value, start_h, end_h):
    if start_h <= end_h:
        return start_h <= hour_value <= end_h
    # Window qua ngày, ví dụ: 22-02
    return hour_value >= start_h or hour_value <= end_h

def is_high_liquidity_time(dt_value=None):
    if not LIQUIDITY_FOCUS_ENABLED:
        return True
    if not LIQUIDITY_WINDOWS_VN:
        return True
    dt = dt_value or now_vn()
    hour_value = int(dt.hour)
    for start_h, end_h in LIQUIDITY_WINDOWS_VN:
        if is_in_hour_window(hour_value, start_h, end_h):
            return True
    return False

def current_max_active_orders(dt_value=None):
    if not LIQUIDITY_FOCUS_ENABLED:
        return max(1, int(MAX_ACTIVE_ORDERS))
    if is_high_liquidity_time(dt_value):
        return max(1, int(HIGH_LIQUIDITY_MAX_ACTIVE_ORDERS))
    return max(1, int(LOW_LIQUIDITY_MAX_ACTIVE_ORDERS))

def passes_quality_gate(signal):
    quality_now = float(signal.get("quality_score", 0) or 0)
    if quality_now < MIN_SIGNAL_QUALITY_SCORE:
        return False, f"Quality thấp ({quality_now:.2f} < {MIN_SIGNAL_QUALITY_SCORE:.2f})"
    return True, f"Quality đạt ({quality_now:.2f})"

def effective_signal_cooldown(signal):
    cooldown = max(10, int(SIGNAL_COOLDOWN_SECONDS))
    quality_now = float(signal.get("quality_score", 0) or 0)
    if quality_now >= HIGH_QUALITY_THRESHOLD:
        factor = max(0.3, min(1.0, float(HIGH_QUALITY_COOLDOWN_FACTOR)))
        cooldown = max(10, int(cooldown * factor))
    return cooldown

def passes_liquidity_focus(signal, dt_value=None):
    if not LIQUIDITY_FOCUS_ENABLED:
        return True, "Liquidity focus tắt"
    if is_high_liquidity_time(dt_value):
        return True, "Đang trong khung giờ thanh khoản cao"

    mode = LIQUIDITY_FOCUS_MODE if LIQUIDITY_FOCUS_MODE in {"soft", "strict"} else "soft"
    if mode == "strict":
        return False, "Ngoài khung giờ thanh khoản cao (strict mode)"

    rr_now = calc_rr_from_levels(signal.get("side"), signal.get("entry"), signal.get("tp"), signal.get("sl"))
    if rr_now is None:
        rr_now = float(signal.get("rr", 0) or 0)
    quality_now = float(signal.get("quality_score", 0) or 0)

    if rr_now < LIQUIDITY_SOFT_MIN_RR:
        return False, f"Ngoài giờ thanh khoản cao: RR {rr_now:.2f} < {LIQUIDITY_SOFT_MIN_RR:.2f}"
    if quality_now < LIQUIDITY_SOFT_MIN_QUALITY:
        return False, f"Ngoài giờ thanh khoản cao: quality {quality_now:.2f} < {LIQUIDITY_SOFT_MIN_QUALITY:.2f}"
    return True, "Ngoài giờ thanh khoản cao nhưng đạt ngưỡng soft"

def format_closed_positions_summary(symbol, total_pnl):
    emoji = "🟢" if total_pnl >= 0 else "🔴"
    return (
        f"{emoji} <b>{symbol}: Đã đóng hết lệnh đang theo dõi</b>\n"
        f"💵 Tổng PnL đã đóng: <b>{total_pnl:+.2f} USDT</b>\n"
        f"⏰ <b>{now_vn().strftime('%d/%m/%Y %H:%M')} (GMT+7)</b>"
    )

def decide_positions_to_close(active_positions, incoming_side, live_price, max_active_orders=MAX_ACTIVE_ORDERS):
    if not active_positions:
        return []

    removable = []
    opposite_positions = [p for p in active_positions if p.get("side") != incoming_side]
    if opposite_positions:
        worst_opposite = min(opposite_positions, key=lambda p: calc_live_pnl(p, live_price))
        removable.append(worst_opposite)

    remaining = len(active_positions) - len(removable)
    if remaining >= max_active_orders:
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
_df_cache = {symbol: {} for symbol in SYMBOLS}; _lock = threading.Lock()
def _bg_fetcher():
    while True:
        try:
            for symbol in SYMBOLS:
                for tf in SIGNAL_INTERVALS:
                    df = fetch_data(symbol, tf)
                    if df is None:
                        continue
                    df = add_indicators(df)
                    if df is None or len(df) == 0 or "close" not in df.columns:
                        continue
                    with _lock:
                        _df_cache[symbol][tf] = df
                    print(f"[BG] Updated {symbol} {tf} | Close: {df['close'].iloc[-1]}")
        except Exception as e:
            print(f"[WARN] _bg_fetcher exception: {e}")
        time.sleep(30)

threading.Thread(target=_bg_fetcher, daemon=True).start()
time.sleep(10) # Đợi dữ liệu lần đầu

vst_bal = bing_client.get_vst_balance() if has_api_credentials() else 0.0
learning_state = load_learning_state()
learning_meta = {"dirty": False, "last_save_ts": time.time()}
active_positions_by_symbol = {symbol: [] for symbol in SYMBOLS}
order_seq_by_symbol = {symbol: 0 for symbol in SYMBOLS}
for symbol in SYMBOLS:
    existing_position = bing_client.get_open_position(symbol) if is_trading_enabled() else None
    if existing_position:
        order_seq_by_symbol[symbol] += 1
        existing_position["label"] = f"LỆNH #{order_seq_by_symbol[symbol]}"
        active_positions_by_symbol[symbol].append(existing_position)
    if is_trading_enabled() and (not active_positions_by_symbol[symbol]):
        bing_client.set_leverage(symbol, "LONG", LEVERAGE)
        bing_client.set_leverage(symbol, "SHORT", LEVERAGE)
send_telegram(format_startup_msg(vst_bal))
if not is_trading_enabled():
    send_telegram(
        "ℹ️ <b>Bot đang chạy ở chế độ READ-ONLY</b>\n"
        "Sẽ phân tích và gửi tín hiệu, nhưng không tự động đặt/đóng lệnh.\n"
        "Để bật auto trade: cung cấp BINGX_API_KEY + BINGX_SECRET_KEY và tắt READ_ONLY_MODE."
    )

last_signal_key_by_symbol = {symbol: None for symbol in SYMBOLS}
last_status_notify_ts_by_symbol = {symbol: time.time() for symbol in SYMBOLS}
last_pnl_notified_pct_by_symbol = {symbol: {} for symbol in SYMBOLS}
closed_cycle_pnl_by_symbol = {symbol: 0.0 for symbol in SYMBOLS}
bootstrapped_signal_by_symbol = {symbol: False for symbol in SYMBOLS}
last_entry_ts_by_symbol = {symbol: {} for symbol in SYMBOLS}
last_skip_reason_by_symbol = {symbol: "Bot vừa khởi động, đang chờ tín hiệu hợp lệ đầu tiên." for symbol in SYMBOLS}
last_wait_log_ts_by_symbol = {symbol: 0.0 for symbol in SYMBOLS}
while True:
    try:
        for symbol in SYMBOLS:
            with _lock:
                symbol_frames = dict(_df_cache.get(symbol) or {})
            if not symbol_frames:
                continue

            active_positions = active_positions_by_symbol[symbol]
            primary_df = symbol_frames.get(INTERVAL)
            if primary_df is None:
                primary_df = next(iter(symbol_frames.values()), None)
            if primary_df is None or len(primary_df) < 3:
                continue
            last_closed = primary_df.iloc[-2]
            candle_time = str(last_closed["datetime"])
            live_price = bing_client.get_last_price(symbol)
            if live_price is None:
                live_price = float(last_closed["close"])

            candidates = []
            for tf in SCALP_INTERVALS:
                df_tf = symbol_frames.get(tf)
                if df_tf is None or len(df_tf) < 3:
                    continue
                scalp_signal = scan_signal(df_tf)
                if scalp_signal:
                    scalp_signal = dict(scalp_signal)
                    scalp_signal["interval"] = tf
                    scalp_signal["strategy"] = "scalp"
                    scalp_signal = apply_learning_to_signal_v2(learning_state, symbol, scalp_signal)
                    candidates.append(scalp_signal)

            for tf in SWING_INTERVALS:
                df_tf = symbol_frames.get(tf)
                if df_tf is None or len(df_tf) < 3:
                    continue
                swing_signal = scan_swing_signal(df_tf)
                if swing_signal:
                    swing_signal = dict(swing_signal)
                    swing_signal["interval"] = tf
                    swing_signal = apply_learning_to_signal_v2(learning_state, symbol, swing_signal)
                    candidates.append(swing_signal)

            if GRID_BOT_ENABLED:
                grid_df = symbol_frames.get(GRID_INTERVAL)
                if grid_df is not None and len(grid_df) >= GRID_MIN_CANDLES:
                    grid_signal = scan_grid_signal(grid_df)
                    if grid_signal:
                        grid_signal = dict(grid_signal)
                        grid_signal["interval"] = GRID_INTERVAL
                        grid_signal = apply_learning_to_signal_v2(learning_state, symbol, grid_signal)
                        candidates.append(grid_signal)

            signal_eval_time = now_vn()
            signal = pick_best_signal(candidates, signal_eval_time)
            if signal:
                active_limit = current_max_active_orders(signal_eval_time)
                if signal.get("source", DATA_SOURCE) != "BINGX":
                    last_skip_reason_by_symbol[symbol] = f"Nguồn tín hiệu không hợp lệ: {signal.get('source')}"
                    print(f"[WARN] Bỏ qua tín hiệu do nguồn không phải BingX: {signal.get('source')}")
                    continue
                quality_ok, quality_reason = passes_quality_gate(signal)
                if not quality_ok:
                    last_skip_reason_by_symbol[symbol] = quality_reason
                    print(f"[INFO] [{symbol}] Bỏ qua tín hiệu: {quality_reason}")
                    continue
                tradeable, tradeable_reason = is_signal_tradeable(signal)
                if not tradeable:
                    last_skip_reason_by_symbol[symbol] = tradeable_reason
                    print(f"[INFO] [{symbol}] Bỏ qua tín hiệu: {tradeable_reason}")
                    continue
                if not is_entry_still_valid(signal, float(live_price)):
                    last_skip_reason_by_symbol[symbol] = f"Giá lệch khỏi entry quá {ENTRY_DRIFT_MAX_PCT:.2f}%."
                    print(f"[INFO] [{symbol}] Bỏ qua: giá đã drift xa khỏi entry signal.")
                    continue
                liquidity_ok, liquidity_reason = passes_liquidity_focus(signal, signal_eval_time)
                if not liquidity_ok:
                    last_skip_reason_by_symbol[symbol] = liquidity_reason
                    print(f"[INFO] [{symbol}] Bỏ qua tín hiệu: {liquidity_reason}")
                    continue
                sig_key = f"{signal['side']}_{signal.get('strategy', 'scalp')}_{signal.get('interval', INTERVAL)}_{signal['candle_time']}"
                signal_bucket = f"{signal['side']}_{signal.get('strategy', 'scalp')}_{signal.get('interval', INTERVAL)}"
                now_ts = time.time()
                last_entry_ts = last_entry_ts_by_symbol[symbol].get(signal_bucket)
                signal_cooldown = effective_signal_cooldown(signal)
                if last_entry_ts and (now_ts - last_entry_ts < signal_cooldown):
                    remaining = int(signal_cooldown - (now_ts - last_entry_ts))
                    last_skip_reason_by_symbol[symbol] = f"Đang cooldown {signal_bucket}, còn {max(remaining, 0)}s."
                    print(
                        f"[INFO] [{symbol}] Bỏ qua tín hiệu mới do cooldown {signal_bucket}: "
                        f"còn {max(remaining, 0)}s (cooldown={signal_cooldown}s)."
                    )
                    continue
                if not bootstrapped_signal_by_symbol[symbol]:
                    if not is_trading_enabled():
                        bootstrapped_signal_by_symbol[symbol] = True
                    else:
                        last_signal_key_by_symbol[symbol] = sig_key
                        bootstrapped_signal_by_symbol[symbol] = True
                        print(
                            f"[INFO] [{symbol}] Bootstrapped signal: {sig_key} - chờ tín hiệu mới để vào lệnh. "
                            f"Lý do chờ: cần nến/tín hiệu mới sau khi bot khởi động lại để tránh vào trùng lệnh cũ."
                        )
                        last_skip_reason_by_symbol[symbol] = "Bootstrap: bỏ qua tín hiệu đầu tiên sau khi restart."
                        continue

                if sig_key != last_signal_key_by_symbol[symbol]:
                    if not is_trading_enabled():
                        order_seq_by_symbol[symbol] += 1
                        order_label = f"LỆNH #{order_seq_by_symbol[symbol]}"
                        send_telegram(format_signal_msg(signal, symbol, order_label))
                        send_telegram("🧪 <b>Bỏ qua đặt lệnh tự động</b>\nLý do: bot đang ở chế độ READ-ONLY.")
                        last_skip_reason_by_symbol[symbol] = "READ-ONLY mode: chỉ gửi tín hiệu, không đặt lệnh."
                        last_signal_key_by_symbol[symbol] = sig_key
                        continue

                    removable_positions = decide_positions_to_close(
                        active_positions, signal["side"], float(live_price), active_limit
                    )
                    for pos in removable_positions:
                        pnl_snapshot = calc_live_pnl(pos, float(live_price))
                        close_resp = bing_client.close_position_market(symbol, pos.get("side"), pos.get("quantity"))
                        close_ok = close_resp and close_resp.get("code") == 0
                        if close_ok:
                            active_positions_by_symbol[symbol] = [x for x in active_positions_by_symbol[symbol] if x.get("label") != pos.get("label")]
                            active_positions = active_positions_by_symbol[symbol]
                            closed_cycle_pnl_by_symbol[symbol] += pnl_snapshot
                            update_learning_state(
                                learning_state, symbol, pos.get("strategy", "scalp"),
                                pos.get("interval", INTERVAL), pos.get("side"), pnl_snapshot
                            )
                            mark_learning_dirty(learning_meta)
                        send_telegram(
                            "🔄 <b>Điều chỉnh danh mục lệnh</b>\n"
                            f"Mã: <b>{symbol}</b>\n"
                            f"Đóng theo phân tích: <b>{pos.get('label')}</b> ({pos.get('side')})\n"
                            f"Kết quả đóng lệnh: <b>{'Thành công' if close_ok else 'Thất bại'}</b>\n"
                            f"PnL tạm tính khi đóng: <b>{pnl_snapshot:+.2f} USDT</b>\n"
                            f"Lý do: ưu tiên tín hiệu mới {signal['side']} và giữ tối đa {active_limit} lệnh.\n"
                            f"Chi tiết API: <b>{(close_resp or {}).get('msg', 'N/A')}</b>"
                        )
                    if (not active_positions) and removable_positions:
                        send_telegram(format_closed_positions_summary(symbol, closed_cycle_pnl_by_symbol[symbol]))
                        closed_cycle_pnl_by_symbol[symbol] = 0.0

                    if len(active_positions) >= active_limit:
                        last_skip_reason_by_symbol[symbol] = (
                            f"Đạt giới hạn lệnh mở ({len(active_positions)}/{active_limit})."
                        )
                        print(
                            f"[INFO] [{symbol}] Đã đạt tối đa số lệnh giữ, bỏ qua tín hiệu mới. "
                            f"Lý do chờ: đang giữ {len(active_positions)}/{active_limit} lệnh, "
                            f"ưu tiên quản trị rủi ro trước khi mở thêm."
                        )
                        last_signal_key_by_symbol[symbol] = sig_key
                        continue

                    order_seq_by_symbol[symbol] += 1
                    order_label = f"LỆNH #{order_seq_by_symbol[symbol]}"
                    send_telegram(format_signal_msg(signal, symbol, order_label))
                    last_signal_key_by_symbol[symbol] = sig_key
                    last_price = float(live_price)
                    print(
                        f"[INFO] [{symbol}] Chuẩn bị vào {order_label} | Side={signal['side']} | "
                        f"Lý do vào lệnh: {build_entry_reason(signal)}"
                    )
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
                    rr_final_tp, rr_final_sl, rr_final_changed = align_tp_sl_with_rr(
                        order_signal["side"], last_price, order_signal.get("tp"), order_signal.get("sl"), signal.get("rr")
                    )
                    order_signal["tp"], order_signal["sl"] = sanitize_tp_sl(
                        order_signal["side"], rr_final_tp, rr_final_sl, last_price
                    )
                    levels_changed = levels_changed or (pre_safe_tp != order_signal["tp"] or pre_safe_sl != order_signal["sl"])
                    levels_changed = levels_changed or rr_final_changed
                    effective_rr = calc_rr_from_levels(order_signal["side"], last_price, order_signal.get("tp"), order_signal.get("sl"))
                    if effective_rr is not None:
                        order_signal["rr"] = effective_rr
                    if levels_changed:
                        send_telegram(
                            "🛠️ <b>Đã hiệu chỉnh TP/SL trước khi vào lệnh</b>\n"
                            f"Mã: <b>{symbol}</b>\n"
                            f"TP: <b>{format_price(order_signal['tp'])}</b> | SL: <b>{format_price(order_signal['sl'])}</b>\n"
                            f"R:R thực tế theo entry: <b>1:{order_signal.get('rr', signal.get('rr', RR)):.2f}</b>\n"
                            f"Tiêu chí: TP tối thiểu {MIN_TP_PCT:.2f}% và SL tối thiểu {MIN_SL_PCT:.2f}% so với entry."
                        )

                    quantity = calc_order_quantity(last_price, ORDER_NOTIONAL_USDT)
                    order = bing_client.place_market_order(
                        symbol, "BUY" if signal['side'] == 'LONG' else "SELL", signal['side'], quantity,
                        order_signal['tp'], order_signal['sl']
                    )
                    print(f"[{symbol}] Order Result: {order}")
                    if order and order.get("code") == 0:
                        last_skip_reason_by_symbol[symbol] = "Đã vào lệnh thành công."
                        last_entry_ts_by_symbol[symbol][signal_bucket] = time.time()
                        fill_price = extract_order_avg_price(order, last_price)
                        send_telegram(format_order_result_msg(order_signal, symbol, order, order_label, fill_price))
                        protection_result = bing_client.add_missing_tp_sl(symbol, signal["side"], order_signal.get("tp"), order_signal.get("sl"))
                        exchange_pos = (protection_result or {}).get("position")
                        if exchange_pos:
                            has_tp = exchange_pos.get("tp") is not None
                            has_sl = exchange_pos.get("sl") is not None
                            if not (has_tp and has_sl):
                                send_telegram(
                                    "⚠️ <b>Cảnh báo:</b> Lệnh đã khớp nhưng chưa thấy đủ TP/SL trên sàn.\n"
                                    f"Mã: <b>{symbol}</b>\n"
                                    f"TP: <b>{'Có' if has_tp else 'Thiếu'}</b> | SL: <b>{'Có' if has_sl else 'Thiếu'}</b>\n"
                                    "Vui lòng kiểm tra lại trên BingX."
                                )
                            elif protection_result.get("tp_added") or protection_result.get("sl_added"):
                                send_telegram(
                                    "🛡️ <b>Đã bổ sung TP/SL sau khi vào lệnh</b>\n"
                                    f"Mã: <b>{symbol}</b>\n"
                                    f"TP thêm mới: <b>{'Có' if protection_result.get('tp_added') else 'Không'}</b> | "
                                    f"SL thêm mới: <b>{'Có' if protection_result.get('sl_added') else 'Không'}</b>"
                                )

                        active_positions_by_symbol[symbol].append({
                            "label": order_label,
                            "side": signal["side"],
                            "strategy": signal.get("strategy", "scalp"),
                            "interval": signal.get("interval", INTERVAL),
                            "entry": fill_price,
                            "quantity": float(quantity),
                            "tp": exchange_pos.get("tp") if exchange_pos and exchange_pos.get("tp") is not None else order_signal.get("tp"),
                            "sl": exchange_pos.get("sl") if exchange_pos and exchange_pos.get("sl") is not None else order_signal.get("sl"),
                            "opened_at": now_vn()
                        })
                        last_pnl_notified_pct_by_symbol[symbol][order_label] = None
                    else:
                        err_msg = (order or {}).get("msg", "Không rõ lỗi")
                        last_skip_reason_by_symbol[symbol] = f"Lỗi đặt lệnh: {err_msg}"
                        send_telegram(
                            "❌ <b>Đặt lệnh thất bại</b>\n"
                            f"Mã: <b>{symbol}</b>\n"
                            f"🆔 Mã lệnh: <b>{order_label}</b>\n"
                            f"Lý do: <b>{err_msg}</b>"
                        )

            elif (not active_positions) and (time.time() - last_status_notify_ts_by_symbol[symbol] >= 3600):
                active_limit = current_max_active_orders(now_vn())
                liquidity_note = ""
                if LIQUIDITY_FOCUS_ENABLED and not is_high_liquidity_time(now_vn()):
                    liquidity_note = (
                        f" Ngoài khung giờ thanh khoản cao ({LIQUIDITY_WINDOWS_VN_RAW}), "
                        "bot sẽ lọc tín hiệu chặt hơn."
                    )
                wait_reason = (
                    f"Chưa có tín hiệu mới phù hợp ở các TF đang theo dõi "
                    f"({', '.join(SIGNAL_INTERVALS)}), bot tiếp tục quan sát để chờ điểm vào có RR/quality tốt."
                    f" Ngưỡng quality tối thiểu hiện tại: {MIN_SIGNAL_QUALITY_SCORE:.2f}, "
                    f"số lệnh tối đa: {active_limit}.{liquidity_note} "
                    f"Lý do skip gần nhất: {last_skip_reason_by_symbol.get(symbol, 'N/A')}"
                )
                print(f"[INFO] [{symbol}] Chưa vào lệnh. Lý do chờ: {wait_reason}")
                send_telegram(format_status_msg(symbol, live_price, candle_time, wait_reason))
                last_status_notify_ts_by_symbol[symbol] = time.time()


            now_ts = time.time()
            should_log_wait = (now_ts - last_wait_log_ts_by_symbol[symbol]) >= max(10, WAIT_LOG_INTERVAL_SECONDS)
            if should_log_wait:
                tf_ready = ", ".join(sorted(symbol_frames.keys())) if symbol_frames else "N/A"
                position_state = (
                    f"đang giữ {len(active_positions)} lệnh"
                    if active_positions else
                    f"chưa có lệnh mở ({len(active_positions)}/{current_max_active_orders(now_vn())})"
                )
                print(
                    f"[STATE] [{symbol}] Bot đang chờ tín hiệu/nến mới | {position_state} | "
                    f"TF cache: {tf_ready} | Giá hiện tại: {float(live_price):.4f} | "
                    f"Lý do chờ gần nhất: {last_skip_reason_by_symbol.get(symbol, 'N/A')}"
                )
                last_wait_log_ts_by_symbol[symbol] = now_ts

            if active_positions:
                if is_trading_enabled():
                    exchange_pos = bing_client.get_open_position(symbol)
                    if not exchange_pos:
                        if active_positions:
                            for pos in active_positions:
                                pnl_snapshot = calc_live_pnl(pos, float(live_price))
                                update_learning_state(
                                    learning_state, symbol, pos.get("strategy", "scalp"),
                                    pos.get("interval", INTERVAL), pos.get("side"), pnl_snapshot
                                )
                            mark_learning_dirty(learning_meta)
                            closed_cycle_pnl_by_symbol[symbol] += sum(
                                calc_live_pnl(pos, float(live_price)) for pos in active_positions
                            )
                        send_telegram(f"✅ <b>{symbol}</b>: Không còn vị thế mở trên BingX\nXóa danh sách lệnh đang theo dõi.")
                        send_telegram(format_closed_positions_summary(symbol, closed_cycle_pnl_by_symbol[symbol]))
                        active_positions_by_symbol[symbol] = []
                        last_pnl_notified_pct_by_symbol[symbol] = {}
                        closed_cycle_pnl_by_symbol[symbol] = 0.0
                        continue
                    else:
                        active_positions_by_symbol[symbol] = [
                            sync_position_levels_from_exchange(pos, exchange_pos)
                            for pos in active_positions_by_symbol[symbol]
                        ]
                        active_positions = active_positions_by_symbol[symbol]

                tracked_labels = {pos.get("label") for pos in active_positions if pos.get("label")}
                last_pnl_notified_pct_by_symbol[symbol] = {
                    label: pct for label, pct in last_pnl_notified_pct_by_symbol[symbol].items() if label in tracked_labels
                }

                for pos in list(active_positions):
                    pos = check_breakeven_condition(pos, float(live_price), symbol)
                    for i, tracked in enumerate(active_positions_by_symbol[symbol]):
                        if tracked.get("label") == pos.get("label"):
                            active_positions_by_symbol[symbol][i] = pos
                            break
                    active_positions = active_positions_by_symbol[symbol]
                    if pos.get("tp") is not None:
                        if pos["side"] == "LONG" and float(live_price) >= float(pos["tp"]):
                            pnl_snapshot = calc_live_pnl(pos, float(live_price))
                            close_resp = bing_client.close_position_market(symbol, pos["side"], pos.get("quantity"))
                            send_telegram(
                                f"🏁 <b>{symbol} - {pos.get('label')} đã chạm TP</b> | Đóng market: <b>{'OK' if (close_resp or {}).get('code') == 0 else 'Fail'}</b>\n"
                                f"💵 PnL khi đóng: <b>{pnl_snapshot:+.2f} USDT</b>"
                            )
                            active_positions_by_symbol[symbol].remove(pos)
                            closed_cycle_pnl_by_symbol[symbol] += pnl_snapshot
                            update_learning_state(
                                learning_state, symbol, pos.get("strategy", "scalp"),
                                pos.get("interval", INTERVAL), pos.get("side"), pnl_snapshot
                            )
                            mark_learning_dirty(learning_meta)
                            last_pnl_notified_pct_by_symbol[symbol].pop(pos.get("label"), None)
                            if not active_positions_by_symbol[symbol]:
                                send_telegram(format_closed_positions_summary(symbol, closed_cycle_pnl_by_symbol[symbol]))
                                closed_cycle_pnl_by_symbol[symbol] = 0.0
                            continue
                        if pos["side"] == "SHORT" and float(live_price) <= float(pos["tp"]):
                            pnl_snapshot = calc_live_pnl(pos, float(live_price))
                            close_resp = bing_client.close_position_market(symbol, pos["side"], pos.get("quantity"))
                            send_telegram(
                                f"🏁 <b>{symbol} - {pos.get('label')} đã chạm TP</b> | Đóng market: <b>{'OK' if (close_resp or {}).get('code') == 0 else 'Fail'}</b>\n"
                                f"💵 PnL khi đóng: <b>{pnl_snapshot:+.2f} USDT</b>"
                            )
                            active_positions_by_symbol[symbol].remove(pos)
                            closed_cycle_pnl_by_symbol[symbol] += pnl_snapshot
                            update_learning_state(
                                learning_state, symbol, pos.get("strategy", "scalp"),
                                pos.get("interval", INTERVAL), pos.get("side"), pnl_snapshot
                            )
                            mark_learning_dirty(learning_meta)
                            last_pnl_notified_pct_by_symbol[symbol].pop(pos.get("label"), None)
                            if not active_positions_by_symbol[symbol]:
                                send_telegram(format_closed_positions_summary(symbol, closed_cycle_pnl_by_symbol[symbol]))
                                closed_cycle_pnl_by_symbol[symbol] = 0.0
                            continue
                    if pos.get("sl") is not None:
                        if pos["side"] == "LONG" and float(live_price) <= float(pos["sl"]):
                            pnl_snapshot = calc_live_pnl(pos, float(live_price))
                            close_resp = bing_client.close_position_market(symbol, pos["side"], pos.get("quantity"))
                            send_telegram(
                                f"🛑 <b>{symbol} - {pos.get('label')} đã chạm SL</b> | Đóng market: <b>{'OK' if (close_resp or {}).get('code') == 0 else 'Fail'}</b>\n"
                                f"💵 PnL khi đóng: <b>{pnl_snapshot:+.2f} USDT</b>"
                            )
                            active_positions_by_symbol[symbol].remove(pos)
                            closed_cycle_pnl_by_symbol[symbol] += pnl_snapshot
                            update_learning_state(
                                learning_state, symbol, pos.get("strategy", "scalp"),
                                pos.get("interval", INTERVAL), pos.get("side"), pnl_snapshot
                            )
                            mark_learning_dirty(learning_meta)
                            last_pnl_notified_pct_by_symbol[symbol].pop(pos.get("label"), None)
                            if not active_positions_by_symbol[symbol]:
                                send_telegram(format_closed_positions_summary(symbol, closed_cycle_pnl_by_symbol[symbol]))
                                closed_cycle_pnl_by_symbol[symbol] = 0.0
                            continue
                        if pos["side"] == "SHORT" and float(live_price) >= float(pos["sl"]):
                            pnl_snapshot = calc_live_pnl(pos, float(live_price))
                            close_resp = bing_client.close_position_market(symbol, pos["side"], pos.get("quantity"))
                            send_telegram(
                                f"🛑 <b>{symbol} - {pos.get('label')} đã chạm SL</b> | Đóng market: <b>{'OK' if (close_resp or {}).get('code') == 0 else 'Fail'}</b>\n"
                                f"💵 PnL khi đóng: <b>{pnl_snapshot:+.2f} USDT</b>"
                            )
                            active_positions_by_symbol[symbol].remove(pos)
                            closed_cycle_pnl_by_symbol[symbol] += pnl_snapshot
                            update_learning_state(
                                learning_state, symbol, pos.get("strategy", "scalp"),
                                pos.get("interval", INTERVAL), pos.get("side"), pnl_snapshot
                            )
                            mark_learning_dirty(learning_meta)
                            last_pnl_notified_pct_by_symbol[symbol].pop(pos.get("label"), None)
                            if not active_positions_by_symbol[symbol]:
                                send_telegram(format_closed_positions_summary(symbol, closed_cycle_pnl_by_symbol[symbol]))
                                closed_cycle_pnl_by_symbol[symbol] = 0.0
                            continue

                    label = pos.get("label", "")
                    pnl_pct = calc_live_pnl_pct(pos, float(live_price))
                    prev_notified_pct = last_pnl_notified_pct_by_symbol[symbol].get(label)
                    if should_notify_pnl_change(prev_notified_pct, pnl_pct, threshold=10.0):
                        send_telegram(f"📌 <b>{symbol}</b>\n" + format_pnl_msg(pos, float(live_price)))
                        last_pnl_notified_pct_by_symbol[symbol][label] = pnl_pct

            maybe_flush_learning_state(learning_state, learning_meta)

        time.sleep(10)
    except Exception as e:
        print(f"Lỗi Main Loop: {e}")
        maybe_flush_learning_state(learning_state, learning_meta, force=True)
        time.sleep(10)
