"""
signals/grid.py — Grid fast scalp engine: vào lệnh khi giá lệch khỏi anchor.
"""
import pandas as pd

from config import (
    GRID_BOT_ENABLED, GRID_ANCHOR_WINDOW, GRID_LEVELS, GRID_STEP_PCT,
    GRID_TP_FACTOR, GRID_SL_FACTOR, GRID_MIN_QUALITY_SCORE, GRID_MIN_ATR_PCT,
    GRID_MIN_CANDLES, MIN_TP_PCT, MIN_SL_PCT, MIN_ORDER_RR, INTERVAL,
)
from utils import calc_rr_from_levels


def scan_grid_signal(df: pd.DataFrame, symbol_frames: dict = None) -> dict | None:
    """
    Grid fast scalp: vào lệnh ngược chiều khi giá lệch khỏi anchor ngắn hạn theo từng nấc lưới.
    Mục tiêu là ăn nhịp hồi nhanh với TP ngắn và SL cố định theo bội số bước lưới.
    """
    if not GRID_BOT_ENABLED or df is None or len(df) < GRID_MIN_CANDLES:
        return None

    latest = df.iloc[-2]
    entry  = float(latest["close"])
    if entry <= 0:
        return None

    anchor_series = df["close"].rolling(window=max(5, GRID_ANCHOR_WINDOW)).mean()
    anchor        = float(anchor_series.iloc[-2]) if not pd.isna(anchor_series.iloc[-2]) else 0.0
    if anchor <= 0:
        return None

    atr_value = float(latest.get("atr", 0) or 0)
    atr_pct   = (atr_value / entry * 100) if atr_value > 0 else 0
    if atr_pct < GRID_MIN_ATR_PCT:
        return None

    deviation_pct = ((entry - anchor) / anchor) * 100.0
    level_size    = max(0.02, GRID_STEP_PCT)
    level_idx     = min(max(0, int(abs(deviation_pct) / level_size)), max(1, GRID_LEVELS))
    if level_idx < 1:
        return None

    side              = "SHORT" if deviation_pct > 0 else "LONG"
    tp_distance_pct   = max(MIN_TP_PCT, level_size * level_idx * max(0.5, GRID_TP_FACTOR))
    sl_distance_pct   = max(MIN_SL_PCT, level_size * level_idx * max(1.0, GRID_SL_FACTOR))
    min_tp_for_rr_pct = sl_distance_pct * max(0.5, MIN_ORDER_RR)
    tp_distance_pct   = max(tp_distance_pct, min_tp_for_rr_pct)

    if side == "LONG":
        tp = round(entry * (1 + tp_distance_pct / 100.0), 2)
        sl = round(entry * (1 - sl_distance_pct / 100.0), 2)
    else:
        tp = round(entry * (1 - tp_distance_pct / 100.0), 2)
        sl = round(entry * (1 + sl_distance_pct / 100.0), 2)

    rr_now  = calc_rr_from_levels(side, entry, tp, sl) or 0
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
