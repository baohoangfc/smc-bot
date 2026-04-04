"""
signals/backtest_v5.py — Engine "backtest_v5": OB-based scalp signal.
"""
import pandas as pd

from config import RR, INTERVAL
from indicators import swing_highs, swing_lows
from signals.smc_core import find_fvg_close_to_price, ht_trend_alignment


def _calc_bv5_quality(ob_dict, rsi_value, atr_value, close_price, ema200_value, side):
    """
    Tính quality_score cho backtest_v5 signal:
    - OB body ratio   (max 1.0)
    - RSI moderate zone: 40-60 tốt nhất (max 0.8)
    - ATR% strength   (max 0.6)
    - EMA200 alignment (max 0.5)
    Tổng tối đa ~3.0 (trên ngưỡng MIN_SIGNAL_QUALITY_SCORE=2.0)
    """
    score = 0.0
    ob_range = ob_dict["hi"] - ob_dict["lo"]
    if ob_range > 0:
        ob_body_ratio = min(ob_range / max(atr_value, 1e-9), 2.0)
        score += min(1.0, ob_body_ratio * 0.5)
    else:
        score += 0.4
    rsi_dist_from_mid = abs(rsi_value - 50.0)
    if rsi_dist_from_mid <= 10:
        score += 0.8
    elif rsi_dist_from_mid <= 20:
        score += 0.55
    else:
        score += 0.25
    atr_pct = (atr_value / max(close_price, 1e-9)) * 100.0
    score += min(0.6, atr_pct * 0.25)
    if (side == "BULL" and close_price >= ema200_value) or (side == "BEAR" and close_price <= ema200_value):
        score += 0.5
    else:
        score += 0.1
    return round(score, 2)


def scan_signal_backtest_v5(df: pd.DataFrame, symbol_frames: dict = None, current_tf: str = None) -> dict | None:
    """
    Engine backtest_v5: phát hiện tín hiệu dựa trên OB + swing break.
    - Xác định swing high/low gần nhất (n=3)
    - Tìm OB (bearish/bullish) gần nhất chưa bị mitigated
    - Xác nhận qua nến confirmation
    """
    if len(df) < 260:
        return None
    i  = len(df) - 2
    sh = swing_highs(df, n=3)
    sl = swing_lows(df,  n=3)
    prev_sh = [h for h in sh if h < i - 1]
    prev_sl = [l for l in sl if l < i - 1]
    if not prev_sh or not prev_sl:
        return None

    c   = float(df["close"].iloc[i])
    sig = None
    if c > float(df["high"].iloc[max(prev_sh)]):
        sig = "BULL"
    elif c < float(df["low"].iloc[max(prev_sl)]):
        sig = "BEAR"
    if not sig:
        return None

    atr   = float(df["atr"].iloc[i])
    ob    = None
    start = max(0, i - 20)

    if sig == "BULL":
        for j in range(i - 1, start - 1, -1):
            o, c2 = float(df["open"].iloc[j]), float(df["close"].iloc[j])
            h, l  = float(df["high"].iloc[j]), float(df["low"].iloc[j])
            body  = abs(c2 - o); rng = h - l
            if c2 < o and rng > 0 and (body / rng) > 0.35 and body > atr * 0.25:
                if not any(float(df["close"].iloc[k]) < l for k in range(j + 1, i)):
                    fvg = find_fvg_close_to_price(df, j + 2, h, "LONG", lookback=3)
                    ob = {"type": "BULL_OB", "hi": h, "lo": l, "mid": (h + l) / 2, "fvg": fvg}
                    break
    else:
        for j in range(i - 1, start - 1, -1):
            o, c2 = float(df["open"].iloc[j]), float(df["close"].iloc[j])
            h, l  = float(df["high"].iloc[j]), float(df["low"].iloc[j])
            body  = abs(c2 - o); rng = h - l
            if c2 > o and rng > 0 and (body / rng) > 0.35 and body > atr * 0.25:
                if not any(float(df["close"].iloc[k]) > h for k in range(j + 1, i)):
                    fvg = find_fvg_close_to_price(df, j + 2, l, "SHORT", lookback=3)
                    ob = {"type": "BEAR_OB", "hi": h, "lo": l, "mid": (h + l) / 2, "fvg": fvg}
                    break
    if not ob:
        return None

    current_tf = current_tf or INTERVAL
    htf_trend = ht_trend_alignment(symbol_frames, current_tf)
    # Phạt quality nếu đánh ngược trend khung lớn
    htf_penalty = 0.0
    if htf_trend:
        if (sig == "BULL" and htf_trend == "BEARISH") or (sig == "BEAR" and htf_trend == "BULLISH"):
            htf_penalty = 0.5

    o   = float(df["open"].iloc[i])
    h   = float(df["high"].iloc[i])
    l   = float(df["low"].iloc[i])
    po  = float(df["open"].iloc[i - 1])
    pc  = float(df["close"].iloc[i - 1])
    rsi    = float(df["rsi"].iloc[i])
    ema200 = float(df["ema200"].iloc[i])

    if ob["type"] == "BULL_OB":
        body    = c - o; rng = h - l
        confirm = (pc < po and c > o and o <= pc and c >= po) or \
                  (c > o and rng > 0 and (body / rng) > 0.55 and c > l + rng * 0.6)
        valid   = c >= ema200 and 35 <= rsi <= 70 and l <= ob["hi"]
        if not (confirm and valid):
            return None
        entry = ob["mid"]; sl_ = ob["lo"] - atr * 0.5; risk = entry - sl_
        if risk <= 0 or risk > atr * 3:
            return None
        tp      = entry + risk * RR
        quality = _calc_bv5_quality(ob, rsi, atr, c, ema200, "BULL") - htf_penalty
        if ob.get("fvg"):
            fvg_size = abs(ob["fvg"]["top"] - ob["fvg"]["bot"])
            quality += 0.2 + min(0.5, (fvg_size / atr) * 0.4)
        return {
            "side": "LONG", "entry": round(entry, 2), "sl": round(sl_, 2), "tp": round(tp, 2),
            "rr": RR, "quality_score": quality,
            "signal_mode": "backtest_v5", "source": "BINGX",
            "candle_time": str(df["datetime"].iloc[i]),
        }
    else:
        body    = o - c; rng = h - l
        confirm = (pc > po and c < o and o >= pc and c <= po) or \
                  (c < o and rng > 0 and (body / rng) > 0.55 and c < h - rng * 0.6)
        valid   = c <= ema200 and 30 <= rsi <= 65 and h >= ob["lo"]
        if not (confirm and valid):
            return None
        entry = ob["mid"]; sl_ = ob["hi"] + atr * 0.5; risk = sl_ - entry
        if risk <= 0 or risk > atr * 3:
            return None
        tp      = entry - risk * RR
        quality = _calc_bv5_quality(ob, rsi, atr, c, ema200, "BEAR") - htf_penalty
        if ob.get("fvg"):
            fvg_size = abs(ob["fvg"]["top"] - ob["fvg"]["bot"])
            quality += 0.2 + min(0.5, (fvg_size / atr) * 0.4)
        return {
            "side": "SHORT", "entry": round(entry, 2), "sl": round(sl_, 2), "tp": round(tp, 2),
            "rr": RR, "quality_score": quality,
            "signal_mode": "backtest_v5", "source": "BINGX",
            "candle_time": str(df["datetime"].iloc[i]),
        }
