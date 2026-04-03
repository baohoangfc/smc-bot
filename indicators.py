"""
indicators.py — Kỹ thuật phân tích: EMA, RSI, ATR, swing high/low.
"""
import numpy as np
import pandas as pd


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema50"]  = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    delta = df["close"].diff()
    gain  = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
    loss  = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi"] = (100 - (100 / (1 + rs))).fillna(50)
    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"]  - df["close"].shift(1)).abs(),
        ),
    )
    df["atr"]     = tr.ewm(span=14, adjust=False).mean()
    df["atr_pct"] = (df["atr"] / df["close"]) * 100
    return df


def swing_highs(df: pd.DataFrame, n: int = 3) -> list[int]:
    idx = []
    for i in range(n, len(df) - n):
        if all(df["high"].iloc[i] > df["high"].iloc[i - j] for j in range(1, n + 1)) and \
           all(df["high"].iloc[i] > df["high"].iloc[i + j] for j in range(1, n + 1)):
            idx.append(i)
    return idx


def swing_lows(df: pd.DataFrame, n: int = 3) -> list[int]:
    idx = []
    for i in range(n, len(df) - n):
        if all(df["low"].iloc[i] < df["low"].iloc[i - j] for j in range(1, n + 1)) and \
           all(df["low"].iloc[i] < df["low"].iloc[i + j] for j in range(1, n + 1)):
            idx.append(i)
    return idx
