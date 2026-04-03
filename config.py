"""
config.py — Tất cả biến cấu hình bot (ENV vars + computed constants).
Import từ đây thay vì khai báo trực tiếp trong bot.py.
"""
import os
import threading
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ===================== API Credentials =====================
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
BINGX_API_KEY    = os.environ.get("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "")
BINGX_URL        = "https://open-api-vst.bingx.com"
DATA_SOURCE      = "BINGX"

# ===================== Symbols =====================
SYMBOLS_RAW = os.environ.get("BINGX_SYMBOLS", "NCCOGOLD2USD-USDT,BTC-USDT")
SYMBOLS     = [s.strip() for s in SYMBOLS_RAW.split(",") if s.strip()]
if not SYMBOLS:
    SYMBOLS = [os.environ.get("BINGX_SYMBOL", "NCCOGOLD2USD-USDT")]
SYMBOL = SYMBOLS[0]

# ===================== Timeframes =====================
INTERVAL            = os.environ.get("INTERVAL", "15m")
SCALP_INTERVALS_RAW = os.environ.get("SCALP_INTERVALS", "5m,15m,1h")
SWING_INTERVALS_RAW = os.environ.get("SWING_INTERVALS", "4h")
VALID_INTERVALS     = {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"}

# ===================== Trade Parameters =====================
RR                  = float(os.environ.get("RR", "2.0"))
ORDER_NOTIONAL_USDT = float(os.environ.get("ORDER_NOTIONAL_USDT", "1000"))
LEVERAGE            = int(os.environ.get("LEVERAGE", "100"))
MAX_ACTIVE_ORDERS   = int(os.environ.get("MAX_ACTIVE_ORDERS", "3"))
MIN_TP_PCT          = float(os.environ.get("MIN_TP_PCT", "0.20"))
MIN_SL_PCT          = float(os.environ.get("MIN_SL_PCT", "0.20"))

# ===================== Signal Quality =====================
SCALP_RR_TARGET            = float(os.environ.get("SCALP_RR_TARGET", "1.4"))
SCALP_RR_MIN               = float(os.environ.get("SCALP_RR_MIN", "1.0"))
SCALP_RR_MAX               = float(os.environ.get("SCALP_RR_MAX", "1.8"))
SCALP_RR_MAX_HIGH_QUALITY  = float(os.environ.get("SCALP_RR_MAX_HIGH_QUALITY", "2.20"))
SCALP_RR_MAX_MED_QUALITY   = float(os.environ.get("SCALP_RR_MAX_MED_QUALITY", "1.80"))
QUALITY_HIGH_THRESHOLD     = float(os.environ.get("QUALITY_HIGH_THRESHOLD", "2.50"))
QUALITY_MED_THRESHOLD      = float(os.environ.get("QUALITY_MED_THRESHOLD", "2.00"))
SCALP_MIN_QUALITY_SCORE    = float(os.environ.get("SCALP_MIN_QUALITY_SCORE", "1.8"))
MIN_SIGNAL_QUALITY_SCORE   = float(os.environ.get("MIN_SIGNAL_QUALITY_SCORE", "2.00"))
HIGH_QUALITY_THRESHOLD     = float(os.environ.get("HIGH_QUALITY_THRESHOLD", "2.60"))
HIGH_QUALITY_COOLDOWN_FACTOR = float(os.environ.get("HIGH_QUALITY_COOLDOWN_FACTOR", "0.70"))
FALLBACK_MIN_QUALITY_SCORE = float(os.environ.get("FALLBACK_MIN_QUALITY_SCORE", "2.50"))

# ===================== Risk / SL =====================
SL_BUFFER_PCT    = float(os.environ.get("SL_BUFFER_PCT", "0.12"))
SWING_LOOKBACK   = int(os.environ.get("SWING_LOOKBACK", "6"))
MIN_RISK_PCT     = float(os.environ.get("MIN_RISK_PCT", "0.15"))
MIN_ATR_PCT      = float(os.environ.get("MIN_ATR_PCT", "0.03"))
TREND_LOOKBACK   = int(os.environ.get("TREND_LOOKBACK", "20"))
MIN_ORDER_RR     = float(os.environ.get("MIN_ORDER_RR", "1.0"))
SWING_RR_TARGET  = float(os.environ.get("SWING_RR_TARGET", "2.4"))

# ===================== Entry / Drift =====================
ENTRY_DRIFT_MAX_PCT        = float(os.environ.get("ENTRY_DRIFT_MAX_PCT", "0.30"))
ENTRY_DRIFT_RISK_FRACTION  = float(os.environ.get("ENTRY_DRIFT_RISK_FRACTION", "0.30"))

# ===================== Signal Engine =====================
ALLOW_FALLBACK_SIGNAL          = os.environ.get("ALLOW_FALLBACK_SIGNAL", "true").lower() == "true"
FALLBACK_REQUIRE_HIGH_LIQUIDITY = os.environ.get("FALLBACK_REQUIRE_HIGH_LIQUIDITY", "true").lower() == "true"
READ_ONLY_MODE  = os.environ.get("READ_ONLY_MODE", "false").lower() == "true"
SIGNAL_ENGINE   = os.environ.get("SIGNAL_ENGINE", "auto").lower()  # auto | strict | backtest_v5
SIGNAL_COOLDOWN_SECONDS = int(os.environ.get("SIGNAL_COOLDOWN_SECONDS", "90"))

# ===================== Breakeven =====================
BE_TRIGGER_PCT = float(os.environ.get("BE_TRIGGER_PCT", "50.0"))
BE_OFFSET_PCT  = float(os.environ.get("BE_OFFSET_PCT", "0.02"))

# ===================== Trailing Stop Loss =====================
TSL_ENABLED        = os.environ.get("TSL_ENABLED", "false").lower() == "true"
TSL_ACTIVATION_PCT = float(os.environ.get("TSL_ACTIVATION_PCT", "30.0"))  # % progress to TP to start trailing
TSL_TRAIL_PCT      = float(os.environ.get("TSL_TRAIL_PCT", "0.15"))        # % trail distance from peak

# ===================== Liquidity Windows =====================
LIQUIDITY_FOCUS_ENABLED    = os.environ.get("LIQUIDITY_FOCUS_ENABLED", "true").lower() == "true"
LIQUIDITY_FOCUS_MODE       = os.environ.get("LIQUIDITY_FOCUS_MODE", "soft").lower()  # soft | strict
LIQUIDITY_WINDOWS_VN_RAW   = os.environ.get("LIQUIDITY_WINDOWS_VN", "14-17,19-23")
LIQUIDITY_SOFT_MIN_RR      = float(os.environ.get("LIQUIDITY_SOFT_MIN_RR", "1.30"))
LIQUIDITY_SOFT_MIN_QUALITY = float(os.environ.get("LIQUIDITY_SOFT_MIN_QUALITY", "2.20"))
HIGH_LIQUIDITY_MAX_ACTIVE_ORDERS = int(os.environ.get("HIGH_LIQUIDITY_MAX_ACTIVE_ORDERS", str(MAX_ACTIVE_ORDERS + 1)))
LOW_LIQUIDITY_MAX_ACTIVE_ORDERS  = int(os.environ.get("LOW_LIQUIDITY_MAX_ACTIVE_ORDERS", str(max(1, MAX_ACTIVE_ORDERS - 1))))

# ===================== Grid Bot =====================
GRID_BOT_ENABLED    = os.environ.get("GRID_BOT_ENABLED", "true").lower() == "true"
GRID_INTERVAL       = os.environ.get("GRID_INTERVAL", "1m").lower()
GRID_ANCHOR_WINDOW  = int(os.environ.get("GRID_ANCHOR_WINDOW", "34"))
GRID_LEVELS         = int(os.environ.get("GRID_LEVELS", "5"))
GRID_STEP_PCT       = float(os.environ.get("GRID_STEP_PCT", "0.18"))
GRID_TP_FACTOR      = float(os.environ.get("GRID_TP_FACTOR", "0.90"))
GRID_SL_FACTOR      = float(os.environ.get("GRID_SL_FACTOR", "1.50"))
GRID_MIN_QUALITY_SCORE = float(os.environ.get("GRID_MIN_QUALITY_SCORE", "2.10"))
GRID_MIN_ATR_PCT    = float(os.environ.get("GRID_MIN_ATR_PCT", "0.03"))
GRID_MIN_CANDLES    = max(20, GRID_ANCHOR_WINDOW + 2)

# ===================== Learning =====================
LEARNING_ENABLED       = os.environ.get("LEARNING_ENABLED", "true").lower() == "true"
LEARNING_FILE          = os.environ.get("LEARNING_FILE", "learning_state.json")
LEARNING_MIN_TRADES    = int(os.environ.get("LEARNING_MIN_TRADES", "5"))
LEARNING_SAVE_INTERVAL = int(os.environ.get("LEARNING_SAVE_INTERVAL", "60"))

# ===================== Firebase Persistence =====================
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON", "")
FIRESTORE_COLLECTION      = os.environ.get("FIRESTORE_COLLECTION", "smc_bot_state")

# ===================== Misc =====================
HTTP_TIMEOUT             = float(os.environ.get("HTTP_TIMEOUT", "15"))
TELEGRAM_DEDUP_WINDOW_SECONDS = float(os.environ.get("TELEGRAM_DEDUP_WINDOW_SECONDS", "20"))
WAIT_LOG_INTERVAL_SECONDS = int(os.environ.get("WAIT_LOG_INTERVAL_SECONDS", "60"))

# ===================== Parsed / Computed =====================
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
            end_h   = int(end_raw.strip())
            if 0 <= start_h <= 23 and 0 <= end_h <= 23:
                windows.append((start_h, end_h))
        except Exception:
            continue
    return windows

def sanitize_intervals(intervals, fallback):
    valid = [i for i in intervals if i in VALID_INTERVALS]
    return valid or fallback

SCALP_INTERVALS  = sanitize_intervals(parse_intervals(SCALP_INTERVALS_RAW, [INTERVAL]), [INTERVAL])
SWING_INTERVALS  = sanitize_intervals(parse_intervals(SWING_INTERVALS_RAW, ["4h"]), ["4h"])
SIGNAL_INTERVALS = list(dict.fromkeys(SCALP_INTERVALS + SWING_INTERVALS))
if GRID_BOT_ENABLED and GRID_INTERVAL in VALID_INTERVALS and GRID_INTERVAL not in SIGNAL_INTERVALS:
    SIGNAL_INTERVALS.append(GRID_INTERVAL)

LIQUIDITY_WINDOWS_VN = parse_hour_windows(LIQUIDITY_WINDOWS_VN_RAW)

# ===================== HTTP Session (shared) =====================
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
