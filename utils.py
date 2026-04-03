"""
utils.py — Các helper function nhỏ được dùng khắp nơi trong bot.
Không import từ các module nội bộ khác (tránh circular imports).
"""
import json
import hashlib
import math
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

from config import (
    RR, MIN_TP_PCT, MIN_SL_PCT, SCALP_RR_MIN, SCALP_RR_MAX_HIGH_QUALITY,
    SCALP_RR_MAX_MED_QUALITY, QUALITY_HIGH_THRESHOLD, QUALITY_MED_THRESHOLD,
    ENTRY_DRIFT_MAX_PCT, ENTRY_DRIFT_RISK_FRACTION, MIN_ORDER_RR,
    TELEGRAM_DEDUP_WINDOW_SECONDS,
)


def now_vn():
    return datetime.utcnow() + timedelta(hours=7)


def format_price(value):
    if value is None:
        return "N/A"
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def format_vn_time(dt_value, fmt="%d/%m/%Y %H:%M"):
    dt = pd.to_datetime(dt_value)
    return dt.strftime(fmt)


def interval_to_minutes(interval):
    mapping = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
    return mapping.get(interval, 15)


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


def _clamp(value, low, high):
    return max(low, min(high, value))


# ==================== TP/SL Calculation ====================

def calc_rr_from_levels(side, entry, tp, sl):
    """Tính RR thực tế từ entry/tp/sl. Trả về None nếu thiếu dữ liệu."""
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
    """Đồng bộ TP/SL theo RR mục tiêu."""
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
            safe_sl = e - min_sl_gap; changed = True
        risk = max(e - safe_sl, min_sl_gap)
        ideal_tp = e + max(min_tp_gap, risk * rr)
        if safe_tp is None or abs(safe_tp - ideal_tp) > 0.01:
            safe_tp = ideal_tp; changed = True
    else:
        if safe_sl is None or safe_sl <= e:
            safe_sl = e + min_sl_gap; changed = True
        risk = max(safe_sl - e, min_sl_gap)
        ideal_tp = e - max(min_tp_gap, risk * rr)
        if safe_tp is None or abs(safe_tp - ideal_tp) > 0.01:
            safe_tp = ideal_tp; changed = True
    return round(safe_tp, 2), round(safe_sl, 2), changed


def normalize_tp_sl_by_entry(side, entry, tp, sl):
    """Bảo vệ TP/SL theo entry (tránh TP quá sát hoặc SL sai phía)."""
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
            safe_sl = e - min_sl_gap; changed = True
        risk = max(e - safe_sl, min_sl_gap)
        ideal_tp = e + max(min_tp_gap, risk * RR)
        if safe_tp is None or safe_tp <= e or (safe_tp - e) < min_tp_gap:
            safe_tp = ideal_tp; changed = True
    else:
        if safe_sl is None or safe_sl <= e:
            safe_sl = e + min_sl_gap; changed = True
        risk = max(safe_sl - e, min_sl_gap)
        ideal_tp = e - max(min_tp_gap, risk * RR)
        if safe_tp is None or safe_tp >= e or (e - safe_tp) < min_tp_gap:
            safe_tp = ideal_tp; changed = True
    return round(safe_tp, 2), round(safe_sl, 2), changed


def sanitize_tp_sl(side, tp, sl, last_price, min_gap=0.5):
    """Validate TP/SL theo giá thị trường hiện tại."""
    if last_price is None:
        return tp, sl
    lp = float(last_price)
    safe_tp = float(tp) if tp is not None else None
    safe_sl = float(sl) if sl is not None else None
    if side == "LONG":
        if safe_tp is not None and safe_tp <= lp: safe_tp = lp + min_gap
        if safe_sl is not None and safe_sl >= lp: safe_sl = lp - min_gap
    else:
        if safe_tp is not None and safe_tp >= lp: safe_tp = lp - min_gap
        if safe_sl is not None and safe_sl <= lp: safe_sl = lp + min_gap
    if safe_tp is not None: safe_tp = round(safe_tp, 2)
    if safe_sl is not None: safe_sl = round(safe_sl, 2)
    return safe_tp, safe_sl


def enforce_tp_sl_safety(side, entry, tp, sl, last_price):
    """Đảm bảo TP/SL hợp lệ cả theo entry lẫn giá thị trường."""
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
        if safe_tp is None or safe_tp < min_tp_from_entry: safe_tp = min_tp_from_entry
        if safe_sl is None or safe_sl > max_sl_from_entry: safe_sl = max_sl_from_entry
    else:
        max_tp_from_entry = e - min_tp_gap
        min_sl_from_entry = e + min_sl_gap
        if safe_tp is None or safe_tp > max_tp_from_entry: safe_tp = max_tp_from_entry
        if safe_sl is None or safe_sl < min_sl_from_entry: safe_sl = min_sl_from_entry
    safe_tp, safe_sl = sanitize_tp_sl(side, safe_tp, safe_sl, last_price, min_gap=min_gap)
    return safe_tp, safe_sl


def get_dynamic_rr_max(quality_score):
    q = float(quality_score or 0)
    if q >= QUALITY_HIGH_THRESHOLD: return SCALP_RR_MAX_HIGH_QUALITY
    if q >= QUALITY_MED_THRESHOLD:  return SCALP_RR_MAX_MED_QUALITY
    return max(SCALP_RR_MIN, SCALP_RR_MAX_MED_QUALITY - 0.3)


def get_entry_drift_limit_pct(signal, max_drift_pct=None):
    cap_pct = float(ENTRY_DRIFT_MAX_PCT if max_drift_pct is None else max_drift_pct)
    entry = float(signal.get("entry") or 0)
    sl    = float(signal.get("sl") or 0)
    if entry <= 0 or sl <= 0:
        return cap_pct
    risk_pct = abs(entry - sl) / entry * 100.0
    risk_based_pct = risk_pct * max(float(ENTRY_DRIFT_RISK_FRACTION), 0.0)
    # Cấu hình để luôn cho phép sai lệch ít nhất là cap_pct (ví dụ 1.0%)
    return max(cap_pct, risk_based_pct)


def is_entry_still_valid(signal, live_price, max_drift_pct=None):
    drift_limit_pct = get_entry_drift_limit_pct(signal, max_drift_pct=max_drift_pct)
    entry = float(signal.get("entry") or 0)
    if entry <= 0 or live_price <= 0:
        return True, drift_limit_pct, 0.0
    drift_pct = abs(live_price - entry) / entry * 100.0
    if drift_pct > drift_limit_pct:
        print(f"[DRIFT] Entry={entry:.2f}, live={live_price:.2f}, drift={drift_pct:.3f}% > max={drift_limit_pct:.2f}% → BỎ QUA")
        return False, drift_limit_pct, drift_pct
    return True, drift_limit_pct, drift_pct


def is_signal_tradeable(signal):
    if not signal:
        return False, "Tín hiệu rỗng"
    side  = signal.get("side")
    entry = signal.get("entry")
    tp    = signal.get("tp")
    sl    = signal.get("sl")
    rr    = calc_rr_from_levels(side, entry, tp, sl)
    if rr is None:
        rr = float(signal.get("rr", 0) or 0)
    if rr <= 0:
        return False, "RR không hợp lệ"
    if rr < MIN_ORDER_RR:
        return False, f"RR thấp ({rr:.2f} < {MIN_ORDER_RR:.2f})"
    if entry is None or tp is None or sl is None:
        return False, "Thiếu entry/TP/SL"
    return True, f"RR={rr:.2f}"


def calc_order_quantity(entry_price, notional_usdt):
    if entry_price is None or entry_price <= 0:
        return 0
    qty = notional_usdt / float(entry_price)
    return round(max(qty, 0), 4)


# ==================== Telegram Dedup ====================

def build_telegram_dedup_keys(msg):
    raw_text = str(msg or "")
    keys = [hashlib.sha256(raw_text.encode("utf-8")).hexdigest()]
    tracked_marker = "Theo dõi lệnh: báo khi ROI"
    marker_idx = raw_text.find(tracked_marker)
    if marker_idx >= 0:
        normalized_text = raw_text[marker_idx:].strip()
        keys.append(hashlib.sha256(normalized_text.encode("utf-8")).hexdigest())
    return keys


# ==================== BingX API Helpers ====================

def parse_trigger_price(raw_value):
    """Chuẩn hóa dữ liệu TP/SL trả về từ API BingX."""
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
            if stripped[0] in {"{", "[", '"'}:
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
            parsed_value.get("stopPrice"), parsed_value.get("price"),
            parsed_value.get("triggerPrice"), parsed_value.get("avgPrice"),
            parsed_value.get("activatePrice"), parsed_value.get("triggerPx"),
            parsed_value.get("stopPx"), parsed_value.get("trigger"),
            parsed_value.get("value")
        )
        if direct is not None:
            return direct
        for key in ["data", "order", "params", "detail", "extra", "takeProfit", "stopLoss", "tpOrder", "slOrder", "trigger"]:
            if key not in parsed_value:
                continue
            found = parse_trigger_price(parsed_value.get(key))
            if found is not None:
                return found
        for value in parsed_value.values():
            found = parse_trigger_price(value)
            if found is not None:
                return found
        return None
    return pick_first_float(parsed_value)
