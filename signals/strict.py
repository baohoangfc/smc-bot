"""
signals/strict.py — Engine "strict": SMC scalp với sweep + MSS + bias filter.
"""
import pandas as pd

from config import (
    RR, INTERVAL, SWING_LOOKBACK, TREND_LOOKBACK, MIN_ATR_PCT,
    SCALP_MIN_QUALITY_SCORE, SCALP_RR_TARGET, MIN_RISK_PCT, SL_BUFFER_PCT,
    ALLOW_FALLBACK_SIGNAL, FALLBACK_MIN_QUALITY_SCORE, FALLBACK_REQUIRE_HIGH_LIQUIDITY,
)
from utils import get_dynamic_rr_max
from indicators import swing_highs, swing_lows
from signals.backtest_v5 import scan_signal_backtest_v5
from signals.smc_core import analyze_structure, ht_trend_alignment


def _is_high_liquidity_time_check():
    """Lazy import để tránh circular dependency với position_mgmt."""
    from position_mgmt import is_high_liquidity_time
    return is_high_liquidity_time


def calc_scalp_tp_sl_v2(df: pd.DataFrame, side: str, entry: float, quality_score: float = 2.0):
    """
    Tính TP/SL theo hướng scalp:
    - SL bám cấu trúc swing gần nhất + buffer nhỏ
    - TP theo RR mục tiêu (dynamic, bị chặn trong [SCALP_RR_MIN, rr_ceiling])
    """
    if len(df) < max(SWING_LOOKBACK + 2, 10):
        return None, None, None
    recent    = df.iloc[-(SWING_LOOKBACK + 2):-1]
    atr_val   = float(df["atr"].iloc[-2]) if "atr" in df.columns and not pd.isna(df["atr"].iloc[-2]) else 0
    entry_f   = float(entry)
    buffer    = max(0.5, entry_f * (SL_BUFFER_PCT / 100.0), atr_val * 0.25)
    min_risk  = max(0.5, entry_f * (MIN_RISK_PCT / 100.0))

    ema_gap_pct = 0.0
    if "ema50" in df.columns and "ema200" in df.columns:
        ema50  = float(df["ema50"].iloc[-2])
        ema200 = float(df["ema200"].iloc[-2])
        base   = max(abs(entry_f), 1e-9)
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

    rr_used   = SCALP_RR_TARGET * trend_factor * vol_factor
    rr_ceiling = get_dynamic_rr_max(quality_score)
    rr_used   = min(max(rr_used, SCALP_MIN_QUALITY_SCORE - 0.8), rr_ceiling)

    if side == "LONG":
        structure_sl = float(recent["low"].min()) - buffer
        risk = max(entry_f - structure_sl, min_risk)
        if atr_val > 0 and risk < atr_val * 0.5:
            risk = atr_val * 0.5
        sl = entry_f - risk
        tp = entry_f + (risk * rr_used)
    else:
        structure_sl = float(recent["high"].max()) + buffer
        risk = max(structure_sl - entry_f, min_risk)
        if atr_val > 0 and risk < atr_val * 0.5:
            risk = atr_val * 0.5
        sl = entry_f + risk
        tp = entry_f - (risk * rr_used)

    return round(tp, 2), round(sl, 2), rr_used


def _scan_signal_fallback_section(df: pd.DataFrame, symbol_frames: dict = None, current_tf: str = None):
    if not ALLOW_FALLBACK_SIGNAL:
        return None
    is_high_liquidity_time = _is_high_liquidity_time_check()
    from utils import now_vn
    if FALLBACK_REQUIRE_HIGH_LIQUIDITY and not is_high_liquidity_time(now_vn()):
        print("[FALLBACK] Ngoài giờ thanh khoản cao → bỏ qua fallback signal")
        return None
    backup_signal = scan_signal_backtest_v5(df, symbol_frames=symbol_frames, current_tf=current_tf)
    if not backup_signal:
        return None
    quality = float(backup_signal.get("quality_score", 0) or 0)
    if quality < FALLBACK_MIN_QUALITY_SCORE:
        print(f"[FALLBACK] quality={quality:.2f} < threshold={FALLBACK_MIN_QUALITY_SCORE:.2f} → bỏ qua")
        return None
    backup_signal = dict(backup_signal)
    backup_signal["signal_mode"] = "backtest_v5_fallback"
    return backup_signal


def scan_signal_strict(df: pd.DataFrame, symbol_frames: dict = None, current_tf: str = None) -> dict | None:
    """
    Tín hiệu scalp SMC strict:
    1) Bias rõ ràng theo EMA50/EMA200 + cấu trúc gần nhất.
    2) Sweep thanh khoản (quét đỉnh/đáy swing gần nhất).
    3) MSS (close phá cấu trúc ngược lại sau sweep).
    4) ATR tối thiểu để tránh vùng nhiễu.
    Engine 'backtest_v5': delegate trực tiếp sang scan_signal_backtest_v5.
    """
    min_bars = max(160, SWING_LOOKBACK + TREND_LOOKBACK + 5)
    if len(df) < min_bars:
        return None

    last_closed = df.iloc[-2]
    prev_closed = df.iloc[-3]
    recent      = df.iloc[-(SWING_LOOKBACK + 3):-2]
    if len(recent) < SWING_LOOKBACK:
        return None

    atr_pct = float(df["atr_pct"].iloc[-2]) if "atr_pct" in df.columns and not pd.isna(df["atr_pct"].iloc[-2]) else 0.0
    if atr_pct < MIN_ATR_PCT:
        return None

    swing_high      = float(recent["high"].max())
    swing_low       = float(recent["low"].min())
    dealing_range_mid = (swing_high + swing_low) / 2.0

    sh = swing_highs(df, n=3); sh = [x for x in sh if x < len(df)-2]
    sl = swing_lows(df, n=3);  sl = [x for x in sl if x < len(df)-2]
    struct = analyze_structure(df, len(df)-2, sh, sl)
    
    current_tf = current_tf or INTERVAL
    htf_trend = ht_trend_alignment(symbol_frames, current_tf)

    ema50  = float(df["ema50"].iloc[-2])
    ema200 = float(df["ema200"].iloc[-2])
    recent_trend_high = float(df["high"].iloc[-(TREND_LOOKBACK + 2):-2].max())
    recent_trend_low  = float(df["low"].iloc[-(TREND_LOOKBACK + 2):-2].min())
    close_price = float(last_closed["close"])

    bullish_bias = ema50 > ema200 and close_price > ema50 and close_price > recent_trend_low
    bearish_bias = ema50 < ema200 and close_price < ema50 and close_price < recent_trend_high

    recent_5_low  = float(df["low"].iloc[-6:-1].min())
    recent_5_high = float(df["high"].iloc[-6:-1].max())
    liquidity_sweep_low  = recent_5_low < swing_low and close_price > swing_low
    liquidity_sweep_high = recent_5_high > swing_high and close_price < swing_high
    
    # MSS now depends on structure change (BOS or CHOCH in the right direction)
    mss_bull = struct.get("bos") == "BULL_BOS" or struct.get("choch") == "BULL_CHOCH"
    mss_bear = struct.get("bos") == "BEAR_BOS" or struct.get("choch") == "BEAR_CHOCH"
    
    # Rút lại mss_bull/bear nhẹ (candle break) nếu structure chưa rõ
    mss_bull_lite = close_price > float(prev_closed["high"])
    mss_bear_lite = close_price < float(prev_closed["low"])

    in_discount = close_price <= dealing_range_mid
    in_premium  = close_price >= dealing_range_mid

    trend_strength = abs(ema50 - ema200) / max(close_price, 1e-9) * 100.0
    quality_score  = 0.0
    quality_score += 1.0 if bullish_bias or bearish_bias else 0.0
    quality_score += 1.0 if liquidity_sweep_low or liquidity_sweep_high else 0.0
    quality_score += 1.2 if mss_bull or mss_bear else (0.8 if mss_bull_lite or mss_bear_lite else 0.0)
    quality_score += min(0.8, trend_strength * 0.8)
    quality_score += min(0.6, atr_pct * 0.25)
    
    # Premium/Discount Quality Modifier (Soft Filter)
    if in_discount and (bullish_bias or mss_bull):
        quality_score += 0.5
    elif in_premium and (bullish_bias or mss_bull):
        quality_score -= 0.5
        
    if in_premium and (bearish_bias or mss_bear):
        quality_score += 0.5
    elif in_discount and (bearish_bias or mss_bear):
        quality_score -= 0.5
    
    if htf_trend:
        if (bullish_bias and htf_trend == "BEARISH") or (bearish_bias and htf_trend == "BULLISH"):
            quality_score -= 0.6
        elif (bullish_bias and htf_trend == "BULLISH") or (bearish_bias and htf_trend == "BEARISH"):
            quality_score += 0.4

    long_strict   = bullish_bias and liquidity_sweep_low  and (mss_bull or mss_bull_lite)
    short_strict  = bearish_bias and liquidity_sweep_high and (mss_bear or mss_bear_lite)
    long_smc_lite = bullish_bias and (mss_bull or mss_bull_lite) and (liquidity_sweep_low or in_discount)
    short_smc_lite = bearish_bias and (mss_bear or mss_bear_lite) and (liquidity_sweep_high or in_premium)

    near_ema50 = abs(close_price - ema50) <= max(0.5, close_price * 0.0035)
    long_fallback  = ALLOW_FALLBACK_SIGNAL and bullish_bias and (mss_bull or mss_bull_lite) and near_ema50
    short_fallback = ALLOW_FALLBACK_SIGNAL and bearish_bias and (mss_bear or mss_bear_lite) and near_ema50

    allow_long = long_strict or (long_smc_lite and quality_score >= SCALP_MIN_QUALITY_SCORE) or \
                 (long_fallback and quality_score >= (SCALP_MIN_QUALITY_SCORE + 0.2))
    if allow_long:
        e  = round(close_price, 2)
        tp, sl, rr_used = calc_scalp_tp_sl_v2(df, "LONG", e, quality_score=quality_score)
        if tp is None or sl is None:
            return None
        mode = "strict" if long_strict else ("smc_lite" if long_smc_lite else "fallback")
        return {
            "side": "LONG", "entry": e, "sl": sl, "tp": tp,
            "rr": rr_used, "quality_score": round(quality_score, 2),
            "signal_mode": mode, "source": "BINGX",
            "candle_time": str(last_closed["datetime"]),
        }

    allow_short = short_strict or (short_smc_lite and quality_score >= SCALP_MIN_QUALITY_SCORE) or \
                  (short_fallback and quality_score >= (SCALP_MIN_QUALITY_SCORE + 0.2))
    if allow_short:
        e  = round(close_price, 2)
        tp, sl, rr_used = calc_scalp_tp_sl_v2(df, "SHORT", e, quality_score=quality_score)
        if tp is None or sl is None:
            return None
        mode = "strict" if short_strict else ("smc_lite" if short_smc_lite else "fallback")
        return {
            "side": "SHORT", "entry": e, "sl": sl, "tp": tp,
            "rr": rr_used, "quality_score": round(quality_score, 2),
            "signal_mode": mode, "source": "BINGX",
            "candle_time": str(last_closed["datetime"]),
        }

    return _scan_signal_fallback_section(df, symbol_frames=symbol_frames, current_tf=current_tf)
