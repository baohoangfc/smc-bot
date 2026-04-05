"""
position_mgmt.py — Quản lý vị thế, PnL, breakeven, trailing stop, liquidity windows.
"""
from datetime import timedelta

from config import (
    MAX_ACTIVE_ORDERS, LIQUIDITY_FOCUS_ENABLED, LIQUIDITY_FOCUS_MODE,
    LIQUIDITY_WINDOWS_VN, LIQUIDITY_SOFT_MIN_RR, LIQUIDITY_SOFT_MIN_QUALITY,
    HIGH_LIQUIDITY_MAX_ACTIVE_ORDERS, LOW_LIQUIDITY_MAX_ACTIVE_ORDERS,
    MIN_SIGNAL_QUALITY_SCORE, SIGNAL_COOLDOWN_SECONDS, HIGH_QUALITY_THRESHOLD,
    HIGH_QUALITY_COOLDOWN_FACTOR, LEVERAGE, MARGIN_STANDARD,
    BE_TRIGGER_PCT, BE_OFFSET_PCT,
    TSL_ENABLED, TSL_ACTIVATION_PCT, TSL_TRAIL_PCT,
    PARTIAL_TP_ROI_THRESHOLD, PARTIAL_TP_QUANTITY_FRACTION,
    INTERVAL,
)
from utils import calc_rr_from_levels, now_vn
from notifications import send_telegram


# ───────────────────────────────────────────
# Liquidity Windows
# ───────────────────────────────────────────

def is_in_hour_window(hour_value: int, start_h: int, end_h: int) -> bool:
    if start_h <= end_h:
        return start_h <= hour_value <= end_h
    return hour_value >= start_h or hour_value <= end_h


def is_high_liquidity_time(dt_value=None) -> bool:
    if not LIQUIDITY_FOCUS_ENABLED:
        return True
    if not LIQUIDITY_WINDOWS_VN:
        return True
    dt         = dt_value or now_vn()
    hour_value = int(dt.hour)
    for start_h, end_h in LIQUIDITY_WINDOWS_VN:
        if is_in_hour_window(hour_value, start_h, end_h):
            return True
    return False


def current_max_active_orders(dt_value=None) -> int:
    if not LIQUIDITY_FOCUS_ENABLED:
        return max(1, int(MAX_ACTIVE_ORDERS))
    if is_high_liquidity_time(dt_value):
        return max(1, int(HIGH_LIQUIDITY_MAX_ACTIVE_ORDERS))
    return max(1, int(LOW_LIQUIDITY_MAX_ACTIVE_ORDERS))


# ───────────────────────────────────────────
# Signal Gates
# ───────────────────────────────────────────

def passes_quality_gate(signal: dict) -> tuple[bool, str]:
    quality_now = float(signal.get("quality_score", 0) or 0)
    if quality_now < MIN_SIGNAL_QUALITY_SCORE:
        return False, f"Quality thấp ({quality_now:.2f} < {MIN_SIGNAL_QUALITY_SCORE:.2f})"
    return True, f"Quality đạt ({quality_now:.2f})"


def effective_signal_cooldown(signal: dict) -> int:
    cooldown    = max(10, int(SIGNAL_COOLDOWN_SECONDS))
    quality_now = float(signal.get("quality_score", 0) or 0)
    if quality_now >= HIGH_QUALITY_THRESHOLD:
        factor   = max(0.3, min(1.0, float(HIGH_QUALITY_COOLDOWN_FACTOR)))
        cooldown = max(10, int(cooldown * factor))
    return cooldown


def passes_liquidity_focus(signal: dict, dt_value=None) -> tuple[bool, str]:
    if not LIQUIDITY_FOCUS_ENABLED:
        return True, "Liquidity focus tắt"
    if is_high_liquidity_time(dt_value):
        return True, "Đang trong khung giờ thanh khoản cao"
    mode = LIQUIDITY_FOCUS_MODE if LIQUIDITY_FOCUS_MODE in {"soft", "strict"} else "soft"
    if mode == "strict":
        return False, "Ngoài khung giờ thanh khoản cao (strict mode)"
    rr_now      = calc_rr_from_levels(signal.get("side"), signal.get("entry"), signal.get("tp"), signal.get("sl"))
    if rr_now is None:
        rr_now = float(signal.get("rr", 0) or 0)
    quality_now = float(signal.get("quality_score", 0) or 0)
    if rr_now < LIQUIDITY_SOFT_MIN_RR:
        return False, f"Ngoài giờ thanh khoản cao: RR {rr_now:.2f} < {LIQUIDITY_SOFT_MIN_RR:.2f}"
    if quality_now < LIQUIDITY_SOFT_MIN_QUALITY:
        return False, f"Ngoài giờ thanh khoản cao: quality {quality_now:.2f} < {LIQUIDITY_SOFT_MIN_QUALITY:.2f}"
    return True, "Ngoài giờ thanh khoản cao nhưng đạt ngưỡng soft"


# ───────────────────────────────────────────
# PnL Calculations
# ───────────────────────────────────────────

def calc_live_pnl(position: dict, last_price: float) -> float:
    # Ưu tiên lấy số PnL thực từ sàn (cho tài khoản VST/Real) để khớp 100% với App
    from_exchange = position.get("unrealizedProfit")
    if from_exchange is not None:
        return float(from_exchange)
        
    side       = position.get("side")
    qty        = float(position.get("quantity", 0) or 0)
    entry      = float(position.get("entry", 0) or 0)
    last_price = float(last_price or 0)
    
    if qty <= 0 or entry <= 0 or last_price <= 0:
        return 0.0
        
    if side == "LONG":
        return (last_price - entry) * qty
    return (entry - last_price) * qty


def calc_position_notional_base(position: dict) -> float:
    qty   = float(position.get("quantity", 0) or 0)
    entry = float(position.get("entry", 0) or 0)
    notional_entry     = abs(entry * qty)
    api_position_value = float(position.get("positionValue", 0) or 0)
    # Nếu không có data từ sàn, fallback về MARGIN_STANDARD * LEVERAGE
    return api_position_value if api_position_value > 0 else (notional_entry if notional_entry > 0 else (MARGIN_STANDARD * LEVERAGE))


def calc_live_pnl_pct(position: dict, last_price: float) -> float:
    """PnL% theo ROI trên ký quỹ (margin), không phải % notional."""
    pnl           = calc_live_pnl(position, last_price)
    notional_base = calc_position_notional_base(position)
    leverage      = float(position.get("leverage", LEVERAGE) or LEVERAGE or 1)
    leverage      = max(leverage, 1.0)
    margin_base   = (notional_base / leverage) if notional_base else 0
    return (pnl / margin_base) * 100 if margin_base else 0


def should_notify_pnl_change(prev_notified_pct, current_pct, threshold=10.0) -> bool:
    if prev_notified_pct is None:
        return True
    return abs(float(current_pct) - float(prev_notified_pct)) >= float(threshold)


# ───────────────────────────────────────────
# Position Sync
# ───────────────────────────────────────────

def sync_position_levels_from_exchange(tracked_position: dict, exchange_position: dict) -> dict:
    """Đồng bộ TP/SL từ vị thế sàn sang position local."""
    if not tracked_position or not exchange_position:
        return tracked_position
    if tracked_position.get("side") != exchange_position.get("side"):
        return tracked_position
    updated = dict(tracked_position)
    for field in ("tp", "sl", "entry", "quantity", "positionValue", "leverage"):
        incoming = exchange_position.get(field)
        current  = updated.get(field)
        if field in ("tp", "sl"):
            if current is None and incoming is not None:
                updated[field] = incoming
            continue
        if (current is None or float(current or 0) <= 0) and incoming is not None:
            updated[field] = incoming
    return updated


# ───────────────────────────────────────────
# Breakeven
# ───────────────────────────────────────────

def check_breakeven_condition(pos: dict, live_price: float, symbol: str = "") -> dict:
    if pos.get("be_activated"):
        return pos

    entry = float(pos.get("entry") or 0)
    tp    = float(pos.get("tp") or 0)
    sl    = float(pos.get("sl") or 0)
    side  = pos.get("side")
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
                pos["sl"]            = new_sl
                pos["be_activated"]  = True
                print(f"[BE] {symbol} {pos.get('label')} LONG: progress={progress_pct:.1f}% → dịch SL lên BE {new_sl:.2f}")
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
                pos["sl"]            = new_sl
                pos["be_activated"]  = True
                print(f"[BE] {symbol} {pos.get('label')} SHORT: progress={progress_pct:.1f}% → dịch SL xuống BE {new_sl:.2f}")
                send_telegram(
                    f"🔒 <b>{symbol} - {pos.get('label')}: Kích hoạt Breakeven</b>\n"
                    f"📉 Lệnh đang lãi {progress_pct:.1f}% tiến đến TP\n"
                    f"🛑 SL mới: <b>{new_sl:.2f}</b> (Breakeven +{BE_OFFSET_PCT:.2f}%)\n"
                    f"⏰ {now_vn().strftime('%d/%m %H:%M')} (GMT+7)"
                )
    return pos


# ───────────────────────────────────────────
# Partial Take Profit (Chốt lãi 50% tại 100% ROI)
# ───────────────────────────────────────────

def check_partial_take_profit(pos: dict, live_price: float, symbol: str = "") -> tuple:
    """
    Kiểm tra xem vị thế có đạt 100% ROI chưa.
    Trả về (should_close_partial_bool, quantity_to_close)
    """
    if pos.get("partial_tp_done"):
        return False, 0

    from position_mgmt import calc_live_pnl_pct
    roi = calc_live_pnl_pct(pos, live_price)
    
    if roi >= PARTIAL_TP_ROI_THRESHOLD:
        qty = float(pos.get("quantity") or 0)
        close_qty = round(qty * PARTIAL_TP_QUANTITY_FRACTION, 4)
        if close_qty > 0:
            return True, close_qty
            
    return False, 0


# ───────────────────────────────────────────
# Trailing Stop Loss
# ───────────────────────────────────────────

def check_trailing_stop(pos: dict, live_price: float, symbol: str = "") -> dict:
    """
    Trailing Stop Loss thực sự: trail SL theo peak sau khi kích hoạt BE.
    - Chỉ kích hoạt sau khi BE đã được set (be_activated=True).
    - Trail SL lên theo peak mới nếu TSL_ENABLED.
    """
    if not TSL_ENABLED:
        return pos
    if not pos.get("be_activated"):
        return pos

    entry = float(pos.get("entry") or 0)
    tp    = float(pos.get("tp") or 0)
    sl    = float(pos.get("sl") or 0)
    side  = pos.get("side")
    if entry <= 0 or tp <= 0 or sl <= 0:
        return pos

    lp = float(live_price)
    if side == "LONG":
        full_range   = tp - entry
        progress_pct = (lp - entry) / full_range * 100.0 if full_range > 0 else 0
        if progress_pct < TSL_ACTIVATION_PCT:
            return pos
            
        # Thắt chặt Trailing Stop nếu đã chốt lời 50%
        trail_pct = 0.08 if pos.get("partial_tp_done") else TSL_TRAIL_PCT
        
        # Trail SL theo peak: SL mới = live_price * (1 - trail_pct/100)
        tsl_candidate = round(lp * (1 - trail_pct / 100.0), 2)
        if tsl_candidate > sl:
            pos = dict(pos)
            pos["sl"] = tsl_candidate
            peak_pct  = round(progress_pct, 1)
            print(f"[TSL] {symbol} {pos.get('label')} LONG: progress={peak_pct}% → trail SL → {tsl_candidate:.2f}")
            send_telegram(
                f"📡 <b>{symbol} - {pos.get('label')}: Trailing SL cập nhật</b>\n"
                f"📈 Progress: {peak_pct}% → TP\n"
                f"🛑 SL trail: <b>{tsl_candidate:.2f}</b> ({TSL_TRAIL_PCT:.2f}% từ giá hiện tại)\n"
                f"⏰ {now_vn().strftime('%d/%m %H:%M')} (GMT+7)"
            )
    else:
        full_range   = entry - tp
        progress_pct = (entry - lp) / full_range * 100.0 if full_range > 0 else 0
        # Thắt chặt Trailing Stop nếu đã chốt lời 50%
        trail_pct = 0.08 if pos.get("partial_tp_done") else TSL_TRAIL_PCT
        
        tsl_candidate = round(lp * (1 + trail_pct / 100.0), 2)
        if tsl_candidate < sl:
            pos = dict(pos)
            pos["sl"] = tsl_candidate
            peak_pct  = round(progress_pct, 1)
            print(f"[TSL] {symbol} {pos.get('label')} SHORT: progress={peak_pct}% → trail SL → {tsl_candidate:.2f}")
            send_telegram(
                f"📡 <b>{symbol} - {pos.get('label')}: Trailing SL cập nhật</b>\n"
                f"📉 Progress: {peak_pct}% → TP\n"
                f"🛑 SL trail: <b>{tsl_candidate:.2f}</b> ({trail_pct:.2f}% từ giá hiện tại)\n"
                f"⏰ {now_vn().strftime('%d/%m %H:%M')} (GMT+7)"
            )
    return pos


# ───────────────────────────────────────────
# Portfolio Management
# ───────────────────────────────────────────

def decide_positions_to_close(
    active_positions: list, incoming_side: str, live_price: float,
    max_active_orders: int = MAX_ACTIVE_ORDERS,
) -> list:
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
    # Deduplicate by label
    uniq = []
    seen = set()
    for pos in removable:
        label = pos.get("label")
        if label in seen:
            continue
        seen.add(label)
        uniq.append(pos)
    return uniq
