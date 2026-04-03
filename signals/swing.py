"""
signals/swing.py — Swing signal engine: dùng backtest_v5 với RR cao hơn.
"""
import pandas as pd

from config import RR, SWING_RR_TARGET, INTERVAL
from signals.backtest_v5 import scan_signal_backtest_v5


def scan_swing_signal(df: pd.DataFrame, symbol_frames: dict = None, current_tf: str = None) -> dict | None:
    """
    Tín hiệu swing: tái dùng logic backtest_v5 và nâng RR mục tiêu.
    """
    base = scan_signal_backtest_v5(df, symbol_frames=symbol_frames, current_tf=current_tf)
    if not base:
        return None

    signal = dict(base)
    signal["strategy"]    = "swing"
    signal["signal_mode"] = "swing_backtest_v5"

    entry = float(signal.get("entry", 0) or 0)
    sl    = float(signal.get("sl", 0) or 0)
    if entry > 0 and sl > 0:
        if signal["side"] == "LONG":
            risk = entry - sl
            if risk > 0:
                rr_used        = max(float(signal.get("rr", RR) or RR), SWING_RR_TARGET)
                signal["rr"]   = rr_used
                signal["tp"]   = round(entry + risk * rr_used, 2)
        else:
            risk = sl - entry
            if risk > 0:
                rr_used        = max(float(signal.get("rr", RR) or RR), SWING_RR_TARGET)
                signal["rr"]   = rr_used
                signal["tp"]   = round(entry - risk * rr_used, 2)

    signal["quality_score"] = round(float(signal.get("quality_score", 2.1)), 2)
    return signal
