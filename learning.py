"""
learning.py — Learning state: track win/loss per strategy-interval-side, adjust quality/RR.
"""
from config import (
    LEARNING_ENABLED, LEARNING_MIN_TRADES, MARGIN_STANDARD, RR,
    INTERVAL,
)
from utils import align_tp_sl_with_rr, _clamp


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
    row["pnl_sum"]  = float(row.get("pnl_sum", 0.0)) + float(pnl)
    row["avg_pnl"]  = row["pnl_sum"] / max(row["trades"], 1)
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
    side     = signal.get("side")
    key      = learning_key(symbol, strategy, interval, side)
    row      = state.get(key)
    if not row or int(row.get("trades", 0)) < LEARNING_MIN_TRADES:
        return signal

    learned_signal = dict(signal)
    win_rate = float(row.get("win_rate", 0.0))
    avg_pnl  = float(row.get("avg_pnl", 0.0))

    norm_base    = max(MARGIN_STANDARD * 0.5, 12.0) # 50% của Margin chuẩn $25 làm mốc chuẩn
    norm_pnl     = avg_pnl / norm_base
    quality_adjust = _clamp((win_rate - 0.5) * 0.8 + norm_pnl * 0.5, -0.6, 0.6)
    learned_signal["quality_score"] = round(float(learned_signal.get("quality_score", 2.0)) + quality_adjust, 2)

    rr_base       = float(learned_signal.get("rr", RR) or RR)
    rr_multiplier = _clamp(1.0 + (win_rate - 0.5) * 0.3, 0.9, 1.12)
    rr_target     = rr_base * rr_multiplier
    tp_new, sl_new, _ = align_tp_sl_with_rr(
        side,
        float(learned_signal.get("entry", 0) or 0),
        learned_signal.get("tp"),
        learned_signal.get("sl"),
        rr_target
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
