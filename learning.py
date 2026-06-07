"""
learning.py — Learning state: track win/loss per strategy-interval-side, adjust quality/RR.
"""
from config import (
    LEARNING_ENABLED, LEARNING_MIN_TRADES, MARGIN_STANDARD, RR,
    INTERVAL, LEARNING_BLOCK_BAD_COMBOS, LEARNING_BAD_COMBO_MIN_TRADES,
    LEARNING_BAD_COMBO_MAX_WIN_RATE, LEARNING_BAD_COMBO_MIN_AVG_PNL,
    LEARNING_LOSS_STREAK_BLOCK, LEARNING_MAX_QUALITY_PENALTY,
)
from utils import align_tp_sl_with_rr, _clamp


def learning_key(symbol, strategy, interval, side):
    return f"{symbol}|{strategy}|{interval}|{side}"


def _default_learning_row(symbol, strategy, interval, side):
    return {
        "symbol": symbol,
        "strategy": strategy,
        "interval": interval,
        "side": side,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "pnl_sum": 0.0,
        "avg_pnl": 0.0,
        "win_rate": 0.0,
        "loss_streak": 0,
        "win_streak": 0,
        "max_loss_streak": 0,
        "last_pnl": 0.0,
    }


def update_learning_state(state, symbol, strategy, interval, side, pnl):
    key = learning_key(symbol, strategy, interval, side)
    row = state.get(key, _default_learning_row(symbol, strategy, interval, side))
    # Backward-compatible defaults for rows saved before streak tracking existed.
    for field, default in _default_learning_row(symbol, strategy, interval, side).items():
        row.setdefault(field, default)

    pnl_value = float(pnl)
    row["trades"] += 1
    row["last_pnl"] = pnl_value
    if pnl_value >= 0:
        row["wins"] += 1
        row["win_streak"] = int(row.get("win_streak", 0)) + 1
        row["loss_streak"] = 0
    else:
        row["losses"] += 1
        row["loss_streak"] = int(row.get("loss_streak", 0)) + 1
        row["win_streak"] = 0
        row["max_loss_streak"] = max(int(row.get("max_loss_streak", 0)), row["loss_streak"])
    row["pnl_sum"]  = float(row.get("pnl_sum", 0.0)) + pnl_value
    row["avg_pnl"]  = row["pnl_sum"] / max(row["trades"], 1)
    row["win_rate"] = float(row["wins"]) / max(row["trades"], 1)
    state[key] = row
    print(
        f"[LEARN] Update {key} | trades={row['trades']} | win_rate={row['win_rate']:.2f} | "
        f"avg_pnl={row['avg_pnl']:+.2f} | loss_streak={row['loss_streak']} | last_pnl={pnl_value:+.2f}"
    )
    return row


def _learning_penalty(row, bayes_win_rate, stable_avg_pnl, norm_base):
    """Return an additional quality penalty for statistically weak buckets."""
    trades = int(row.get("trades", 0))
    if trades < LEARNING_MIN_TRADES:
        return 0.0

    pnl_penalty = 0.0
    if stable_avg_pnl < 0:
        pnl_penalty = min(0.45, abs(stable_avg_pnl) / max(norm_base, 1e-9) * 0.45)

    win_rate_penalty = 0.0
    if bayes_win_rate < 0.48:
        win_rate_penalty = min(0.45, (0.48 - bayes_win_rate) * 1.4)

    streak_penalty = 0.0
    loss_streak = int(row.get("loss_streak", 0))
    if loss_streak >= 2:
        streak_penalty = min(0.40, 0.12 * (loss_streak - 1))

    return min(float(LEARNING_MAX_QUALITY_PENALTY), pnl_penalty + win_rate_penalty + streak_penalty)


def _is_bad_combo_blocked(row):
    if not LEARNING_BLOCK_BAD_COMBOS:
        return False, ""

    trades = int(row.get("trades", 0))
    win_rate = float(row.get("win_rate", 0.0))
    avg_pnl = float(row.get("avg_pnl", 0.0))
    pnl_sum = float(row.get("pnl_sum", 0.0))
    loss_streak = int(row.get("loss_streak", 0))

    if loss_streak >= max(1, int(LEARNING_LOSS_STREAK_BLOCK)):
        return True, f"learning block: loss_streak={loss_streak} >= {LEARNING_LOSS_STREAK_BLOCK}"

    if (
        trades >= max(1, int(LEARNING_BAD_COMBO_MIN_TRADES))
        and win_rate <= float(LEARNING_BAD_COMBO_MAX_WIN_RATE)
        and avg_pnl < float(LEARNING_BAD_COMBO_MIN_AVG_PNL)
        and pnl_sum < 0
    ):
        return True, (
            "learning block: combo kém "
            f"trades={trades}, win_rate={win_rate:.2f}, avg_pnl={avg_pnl:+.2f}, pnl_sum={pnl_sum:+.2f}"
        )
    return False, ""


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
    trades = int(row.get("trades", 0))
    wins = int(row.get("wins", 0))
    win_rate = float(row.get("win_rate", 0.0))
    avg_pnl  = float(row.get("avg_pnl", 0.0))
    loss_streak = int(row.get("loss_streak", 0))

    # Bayesian smoothing để giảm overfit khi số mẫu còn ít
    prior_n = 8.0
    bayes_win_rate = (wins + 0.5 * prior_n) / max(trades + prior_n, 1.0)
    # Shrink avg_pnl về 0 khi ít lệnh
    confidence = _clamp(trades / 40.0, 0.0, 1.0)
    stable_avg_pnl = avg_pnl * confidence

    norm_base    = max(MARGIN_STANDARD * 0.5, 12.0) # 50% của Margin chuẩn $25 làm mốc chuẩn
    norm_pnl     = stable_avg_pnl / norm_base
    quality_adjust = _clamp((bayes_win_rate - 0.5) * 0.8 + norm_pnl * 0.5, -0.6, 0.6)
    quality_penalty = _learning_penalty(row, bayes_win_rate, stable_avg_pnl, norm_base)
    quality_adjust = _clamp(quality_adjust - quality_penalty, -float(LEARNING_MAX_QUALITY_PENALTY), 0.6)
    learned_signal["quality_score"] = round(float(learned_signal.get("quality_score", 2.0)) + quality_adjust, 2)

    rr_base       = float(learned_signal.get("rr", RR) or RR)
    rr_multiplier = _clamp(1.0 + (bayes_win_rate - 0.5) * 0.3 + max(norm_pnl, -0.3) * 0.05, 0.85, 1.12)
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

    blocked, block_reason = _is_bad_combo_blocked(row)
    if blocked:
        learned_signal["learning_blocked"] = True
        learned_signal["learning_block_reason"] = block_reason

    learned_signal["learning_note"] = (
        f"win_rate={win_rate:.2f}, bayes_wr={bayes_win_rate:.2f}, avg_pnl={avg_pnl:+.2f}, "
        f"stable_avg_pnl={stable_avg_pnl:+.2f}, norm_base={norm_base:.1f}, "
        f"loss_streak={loss_streak}, quality_adj={quality_adjust:+.2f}, "
        f"quality_penalty={quality_penalty:.2f}, rr_mul={rr_multiplier:.2f}"
    )
    print(f"[LEARN v2] Apply {key} | {learned_signal['learning_note']}")
    if blocked:
        print(f"[LEARN v2] Block {key} | {block_reason}")
    return learned_signal
