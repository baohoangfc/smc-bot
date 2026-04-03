import pandas as pd

def is_bullish_fvg(df: pd.DataFrame, idx: int) -> bool:
    """
    Check if a bullish FVG exists at idx-1.
    A bullish FVG requires: Candle 1 High < Candle 3 Low (where idx is Candle 3).
    """
    if idx < 2 or idx >= len(df):
        return False
    c1_high = float(df["high"].iloc[idx - 2])
    c3_low  = float(df["low"].iloc[idx])
    return c1_high < c3_low

def is_bearish_fvg(df: pd.DataFrame, idx: int) -> bool:
    """
    Check if a bearish FVG exists at idx-1.
    A bearish FVG requires: Candle 1 Low > Candle 3 High.
    """
    if idx < 2 or idx >= len(df):
        return False
    c1_low  = float(df["low"].iloc[idx - 2])
    c3_high = float(df["high"].iloc[idx])
    return c1_low > c3_high

def find_fvg_close_to_price(df: pd.DataFrame, i: int, price: float, side: str, lookback: int = 15):
    """
    Quét ngược tìm FVG gần nhất hỗ trợ cho direction (side).
    side="LONG": Tìm bullish FVG (gap up) dưới chân giá (hoặc gần giá).
    """
    start = max(2, i - lookback)
    for j in range(i, start - 1, -1):
        if side == "LONG":
            if is_bullish_fvg(df, j):
                fvg_bot = float(df["high"].iloc[j-2])
                fvg_top = float(df["low"].iloc[j])
                return {"top": fvg_top, "bot": fvg_bot, "mid": (fvg_top + fvg_bot)/2}
        else:
            if is_bearish_fvg(df, j):
                fvg_top = float(df["low"].iloc[j-2])
                fvg_bot = float(df["high"].iloc[j])
                return {"top": fvg_top, "bot": fvg_bot, "mid": (fvg_top + fvg_bot)/2}
    return None

def analyze_structure(df: pd.DataFrame, i: int, sh: list[int], sl: list[int]):
    """
    Phân tích cấu trúc BOS/CHOCH dựa vào swing highs (sh) và swing lows (sl).
    Trả về dict chứa trạng thái cấu trúc.
    """
    if not sh or not sl:
        return {"bos": None, "choch": None, "trend": "SIDEWAY"}
        
    last_sh_idx = sh[-1]
    last_sl_idx = sl[-1]
    last_sh_val = float(df["high"].iloc[last_sh_idx])
    last_sl_val = float(df["low"].iloc[last_sl_idx])
    close_price = float(df["close"].iloc[i])
    
    # Giả định trend hiện tại dựa vào thứ tự xuất hiện của swing (cái nào mới hơn)
    current_trend = "BULLISH" if last_sh_idx > last_sl_idx else "BEARISH"
    bos, choch = None, None
    
    if current_trend == "BULLISH":
        if close_price > last_sh_val:
            bos = "BULL_BOS"
        elif close_price < last_sl_val:
            choch = "BEAR_CHOCH"
    else:
        if close_price < last_sl_val:
            bos = "BEAR_BOS"
        elif close_price > last_sh_val:
            choch = "BULL_CHOCH"
            
    return {"bos": bos, "choch": choch, "trend": current_trend}

def ht_trend_alignment(symbol_frames: dict, current_tf: str) -> str:
    """
    Xác định xu hướng của khung thời gian lớn hơn liền kề (EMA alignment).
    Trả về "BULLISH", "BEARISH", "SIDEWAY", hoặc None nếu thiếu data.
    """
    if not symbol_frames:
        return None
        
    htf_map = {
        "1m": "5m",
        "3m": "15m",
        "5m": "15m",
        "15m": "1h",
        "30m": "4h",
        "1h": "4h",
        "4h": "1d",
    }
    target_htf = htf_map.get(current_tf, "4h")
    df_htf = symbol_frames.get(target_htf)
    if df_htf is None or len(df_htf) < 5 or "ema50" not in df_htf.columns or "ema200" not in df_htf.columns:
        # Nếu không có khung lớn hơn thì bỏ qua
        return None
        
    ema50  = float(df_htf["ema50"].iloc[-2])
    ema200 = float(df_htf["ema200"].iloc[-2])
    close_price = float(df_htf["close"].iloc[-2])
    
    if ema50 > ema200 and close_price > ema50:
        return "BULLISH"
    elif ema50 < ema200 and close_price < ema50:
        return "BEARISH"
    return "SIDEWAY"
