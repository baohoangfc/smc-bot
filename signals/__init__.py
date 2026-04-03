"""
signals/__init__.py — Public API của signals package.
"""
from signals.backtest_v5 import scan_signal_backtest_v5
from signals.strict import scan_signal_strict
from signals.swing import scan_swing_signal
from signals.grid import scan_grid_signal

from config import SIGNAL_ENGINE, INTERVAL
from utils import calc_rr_from_levels, now_vn


def resolve_signal_engine() -> str:
    """
    auto:
    - Nếu bot chỉ cảnh báo (read-only) => ưu tiên backtest_v5.
    - Nếu bot auto-trade => ưu tiên strict để lọc tín hiệu chặt hơn.
    """
    if SIGNAL_ENGINE in ("strict", "backtest_v5"):
        return SIGNAL_ENGINE
    from config import READ_ONLY_MODE, BINGX_API_KEY, BINGX_SECRET_KEY
    is_trading = not READ_ONLY_MODE and bool(BINGX_API_KEY and BINGX_SECRET_KEY)
    return "backtest_v5" if not is_trading else "strict"


def scan_signal(df, symbol_frames=None, current_tf=None) -> dict | None:
    """
    Router chính: chọn engine dựa theo SIGNAL_ENGINE config.
    """
    if resolve_signal_engine() == "backtest_v5":
        return scan_signal_backtest_v5(df, symbol_frames=symbol_frames, current_tf=current_tf)
    return scan_signal_strict(df, symbol_frames=symbol_frames, current_tf=current_tf)


def signal_priority_score(signal: dict, dt_value=None) -> float:
    from position_mgmt import is_high_liquidity_time
    quality    = float(signal.get("quality_score", 0) or 0)
    rr_value   = float(signal.get("rr", 0) or 0)
    strategy   = signal.get("strategy", "scalp")
    rr_component = min(max(rr_value, 0.0), 3.0)
    score      = quality * 1.2 + rr_component * 0.8
    if is_high_liquidity_time(dt_value):
        score += 0.25 if strategy == "scalp" else 0.10
    else:
        score += 0.25 if strategy == "swing" else 0.05
    return round(score, 4)


def pick_best_signal(signal_candidates: list, dt_value=None) -> dict | None:
    if not signal_candidates:
        return None
    return max(
        signal_candidates,
        key=lambda s: (
            signal_priority_score(s, dt_value),
            float(s.get("quality_score", 0) or 0),
            float(s.get("rr", 0) or 0),
            1 if s.get("strategy") == "swing" else 0,
        ),
    )


__all__ = [
    "scan_signal_backtest_v5",
    "scan_signal_strict",
    "scan_swing_signal",
    "scan_grid_signal",
    "scan_signal",
    "resolve_signal_engine",
    "signal_priority_score",
    "pick_best_signal",
]
