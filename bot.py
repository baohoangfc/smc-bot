import requests
import pandas as pd
import numpy as np
import time
import os
from datetime import datetime, timedelta

# ==========================================
# CONFIG - ĐỌC TỪ ENVIRONMENT VARIABLES
# ==========================================
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
INTERVAL         = os.environ.get("INTERVAL", "15m")
CHECK_SECS       = int(os.environ.get("CHECK_SECS", "450"))
RR               = float(os.environ.get("RR", "2.0"))

# ==========================================
# TELEGRAM
# ==========================================
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM] {msg[:80]}...")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        print(f"Telegram loi: {e}")

# ==========================================
# DATA FETCH
# ==========================================
def fetch_data(interval="15m", candles=500):
    """Dùng Yahoo Finance - không bị block"""
    # Map interval sang Yahoo format
    yf_map = {
        "1m":"1m","3m":"2m","5m":"5m","15m":"15m",
        "30m":"30m","1h":"60m","4h":"60m"
    }
    yf_interval = yf_map.get(interval, "15m")
    # Tính period cần thiết
    period_map = {
        "1m":"7d","2m":"60d","5m":"60d","15m":"60d",
        "30m":"60d","60m":"730d"
    }
    period = period_map.get(yf_interval, "60d")

    url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
    params = {
        "interval": yf_interval,
        "range":    period,
        "events":   "history"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    try:
        r   = requests.get(url, params=params, headers=headers, timeout=15)
        raw = r.json()
        result = raw.get("chart", {}).get("result", [])
        if not result: 
            print(f"Yahoo tra ve rong")
            return None
        ts     = result[0]["timestamp"]
        ohlcv  = result[0]["indicators"]["quote"][0]
        df = pd.DataFrame({
            "datetime": pd.to_datetime(ts, unit="s") + pd.Timedelta(hours=7),
            "open":     ohlcv["open"],
            "high":     ohlcv["high"],
            "low":      ohlcv["low"],
            "close":    ohlcv["close"],
            "volume":   ohlcv["volume"]
        })
        df = df.dropna().reset_index(drop=True)
        # Lấy N nến cuối
        df = df.tail(candles).reset_index(drop=True)
        print(f"Data OK: {len(df)} nen | GC=F (Yahoo)")
        return df
    except Exception as e:
        print(f"Yahoo loi: {e}")
        return None

# ==========================================
# INDICATORS
# ==========================================
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi_calc(s, n=14):
    d = s.diff()
    g = d.where(d>0, 0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.where(d<0, 0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - (100 / (1 + g/l))

def add_indicators(df):
    df['ema50']  = ema(df['close'], 50)
    df['ema200'] = ema(df['close'], 200)
    df['rsi']    = rsi_calc(df['close'], 14)
    df['tr']     = np.maximum(df['high']-df['low'],
                   np.maximum(abs(df['high']-df['close'].shift(1)),
                              abs(df['low'] -df['close'].shift(1))))
    df['atr']    = df['tr'].ewm(span=14, adjust=False).mean()
    return df

# ==========================================
# SMC CORE
# ==========================================
def swing_highs(df, n=3):
    idx = []
    for i in range(n, len(df)-n):
        if all(df['high'].iloc[i]>df['high'].iloc[i-j] for j in range(1,n+1)) and \
           all(df['high'].iloc[i]>df['high'].iloc[i+j] for j in range(1,n+1)):
            idx.append(i)
    return idx

def swing_lows(df, n=3):
    idx = []
    for i in range(n, len(df)-n):
        if all(df['low'].iloc[i]<df['low'].iloc[i-j] for j in range(1,n+1)) and \
           all(df['low'].iloc[i]<df['low'].iloc[i+j] for j in range(1,n+1)):
            idx.append(i)
    return idx

def detect_structure(df, sh, sl, i):
    c = df['close'].iloc[i]
    psh = [h for h in sh if h < i-1]
    psl = [l for l in sl if l < i-1]
    if not psh or not psl: return None
    if c > df['high'].iloc[max(psh)]: return "BULL"
    if c < df['low'].iloc[max(psl)]:  return "BEAR"
    return None

def find_ob(df, sig, i, lookback=20):
    atr   = df['atr'].iloc[i]
    start = max(0, i-lookback)
    if sig == "BULL":
        for j in range(i-1, start-1, -1):
            o,c_,h_,l_ = df['open'].iloc[j],df['close'].iloc[j],df['high'].iloc[j],df['low'].iloc[j]
            body = abs(c_-o); rng = h_-l_
            if c_<o and rng>0 and body/rng>0.35 and body>atr*0.25:
                if not any(df['close'].iloc[k]<l_ for k in range(j+1,i)):
                    return {'type':'BULL_OB','hi':h_,'lo':l_,'mid':(h_+l_)/2}
    elif sig == "BEAR":
        for j in range(i-1, start-1, -1):
            o,c_,h_,l_ = df['open'].iloc[j],df['close'].iloc[j],df['high'].iloc[j],df['low'].iloc[j]
            body = abs(c_-o); rng = h_-l_
            if c_>o and rng>0 and body/rng>0.35 and body>atr*0.25:
                if not any(df['close'].iloc[k]>h_ for k in range(j+1,i)):
                    return {'type':'BEAR_OB','hi':h_,'lo':l_,'mid':(h_+l_)/2}
    return None

def bull_confirm(df, i):
    o,c_,h_,l_ = df['open'].iloc[i],df['close'].iloc[i],df['high'].iloc[i],df['low'].iloc[i]
    po,pc = df['open'].iloc[i-1],df['close'].iloc[i-1]
    body=c_-o; rng=h_-l_
    if pc<po and c_>o and o<=pc and c_>=po: return True
    if c_>o and rng>0 and body/rng>0.55 and c_>l_+rng*0.6: return True
    return False

def bear_confirm(df, i):
    o,c_,h_,l_ = df['open'].iloc[i],df['close'].iloc[i],df['high'].iloc[i],df['low'].iloc[i]
    po,pc = df['open'].iloc[i-1],df['close'].iloc[i-1]
    body=o-c_; rng=h_-l_
    if pc>po and c_<o and o>=pc and c_<=po: return True
    if c_<o and rng>0 and body/rng>0.55 and c_<h_-rng*0.6: return True
    return False

def valid_long(df, i, ob):
    c=df['close'].iloc[i]; r=df['rsi'].iloc[i]
    if c < df['ema200'].iloc[i]: return False
    if r<35 or r>70: return False
    if df['low'].iloc[i] > ob['hi']: return False
    return True

def valid_short(df, i, ob):
    c=df['close'].iloc[i]; r=df['rsi'].iloc[i]
    if c > df['ema200'].iloc[i]: return False
    if r<30 or r>65: return False
    if df['high'].iloc[i] < ob['lo']: return False
    return True

def sltp_long(df, i, ob):
    atr=df['atr'].iloc[i]; entry=ob['mid']
    sl=ob['lo']-atr*0.5; risk=entry-sl
    if risk<=0 or risk>atr*3: return None,None,None
    return entry, sl, entry+risk*RR

def sltp_short(df, i, ob):
    atr=df['atr'].iloc[i]; entry=ob['mid']
    sl=ob['hi']+atr*0.5; risk=sl-entry
    if risk<=0 or risk>atr*3: return None,None,None
    return entry, sl, entry-risk*RR

def scan_signal(df):
    sh = swing_highs(df, n=3)
    sl = swing_lows(df,  n=3)
    for i in range(len(df)-1, max(len(df)-80, 10), -1):
        sig = detect_structure(df, sh, sl, i)
        if not sig: continue
        ob = find_ob(df, sig, i, lookback=20)
        if not ob: continue
        if ob['type']=='BULL_OB' and valid_long(df,i,ob) and bull_confirm(df,i):
            e,sl_,tp = sltp_long(df,i,ob)
            if e: return {'side':'LONG','entry':e,'sl':sl_,'tp':tp,'candle_time':str(df['datetime'].iloc[i])}
        elif ob['type']=='BEAR_OB' and valid_short(df,i,ob) and bear_confirm(df,i):
            e,sl_,tp = sltp_short(df,i,ob)
            if e: return {'side':'SHORT','entry':e,'sl':sl_,'tp':tp,'candle_time':str(df['datetime'].iloc[i])}
    return None

# ==========================================
# FORMAT TIN TELEGRAM
# ==========================================
def format_msg(signal, price, tf):
    side   = signal['side']
    emoji  = "🟢" if side=="LONG" else "🔴"
    action = "MUA (LONG)" if side=="LONG" else "BAN (SHORT)"
    entry  = round(signal['entry'], 2)
    sl     = round(signal['sl'], 2)
    tp     = round(signal['tp'], 2)
    rr_r   = round(abs(tp-entry)/max(abs(entry-sl),0.01), 2)
    now    = datetime.now().strftime('%d/%m/%Y %H:%M')
    return (
        f"{emoji} <b>TIN HIEU SMC - XAUUSDT {tf}</b>\n\n"
        f"Lenh      : <b>{action}</b>\n"
        f"Gia HT    : <b>{price}</b>\n"
        f"Entry     : <b>{entry}</b>\n"
        f"Stop Loss : <b>{sl}</b>\n"
        f"Take Profit: <b>{tp}</b>\n"
        f"R:R       : <b>1:{rr_r}</b>\n\n"
        f"<i>{now} (GMT+7)</i>\n"
        f"<i>Chi tham khao, tu xac nhan truoc khi vao lenh</i>"
    )

# ==========================================
# MAIN LOOP
# ==========================================
print(f"Bot SMC khoi dong | {INTERVAL} | Check moi {CHECK_SECS}s")
send_telegram(
    f"<b>Bot SMC XAUUSDT {INTERVAL} da khoi dong</b>\n"
    f"Check moi {CHECK_SECS//60} phut\n"
    f"{datetime.now().strftime('%d/%m/%Y %H:%M')} (GMT+7)"
)

last_signal_key  = None
last_status_time = datetime.now() - timedelta(minutes=10)  # Gửi ngay lần đầu
STATUS_INTERVAL  = 10 * 60  # 10 phút

while True:
    try:
        now_str = datetime.now().strftime('%H:%M:%S')
        df = fetch_data(INTERVAL, candles=500)

        if df is None:
            print(f"[{now_str}] Khong tai duoc data")
            # Vẫn gửi status nếu đến giờ
            elapsed = (datetime.now() - last_status_time).total_seconds()
            if elapsed >= STATUS_INTERVAL:
                send_telegram(
                    f"⚠️ <b>SMC Bot - Canh bao</b>\n\n"
                    f"Khong tai duoc du lieu XAUUSDT\n"
                    f"Dang thu lai...\n\n"
                    f"<i>{datetime.now().strftime('%d/%m/%Y %H:%M')} (GMT+7)</i>"
                )
                last_status_time = datetime.now()
            time.sleep(60)
            continue

        df    = add_indicators(df)
        price = round(df['close'].iloc[-1], 2)
        signal = scan_signal(df)

        # --- GỬI TÍN HIỆU MỚI ---
        if signal:
            sig_key = f"{signal['side']}_{signal['candle_time']}"
            if sig_key != last_signal_key:
                msg = format_msg(signal, price, INTERVAL)
                send_telegram(msg)
                print(f"[{now_str}] TIN HIEU {signal['side']} | Entry:{round(signal['entry'],2)} SL:{round(signal['sl'],2)} TP:{round(signal['tp'],2)}")
                last_signal_key = sig_key
            else:
                print(f"[{now_str}] Gia:{price} | Tin hieu cu ({signal['side']}) - bo qua")
        else:
            print(f"[{now_str}] Gia:{price} | Chua co tin hieu")

        # --- GỬI TRẠNG THÁI MỖI 10 PHÚT ---
        elapsed = (datetime.now() - last_status_time).total_seconds()
        if elapsed >= STATUS_INTERVAL:
            if signal:
                sig_side  = "LONG (MUA)" if signal['side']=="LONG" else "SHORT (BAN)"
                sig_emoji = "🟢" if signal['side']=="LONG" else "🔴"
                sig_info  = (
                    f"{sig_emoji} Co tin hieu: <b>{sig_side}</b>\n"
                    f"   Entry : <b>{round(signal['entry'],2)}</b>\n"
                    f"   SL    : <b>{round(signal['sl'],2)}</b>\n"
                    f"   TP    : <b>{round(signal['tp'],2)}</b>\n"
                    f"   (Chua vao lenh - dang cho retest)"
                )
            else:
                sig_info = "⏳ Chua co tin hieu. Dang theo doi..."

            status_msg = (
                f"🤖 <b>SMC Bot - Cap nhat {datetime.now().strftime('%H:%M')} (GMT+7)</b>\n\n"
                f"Gia XAUUSDT : <b>{price}</b>\n"
                f"Khung TG    : <b>{INTERVAL}</b>\n"
                f"Trang thai  : ✅ Dang chay\n\n"
                f"{sig_info}\n\n"
                f"<i>Cap nhat tiep theo luc {(datetime.now() + timedelta(minutes=10)).strftime('%H:%M')}</i>"
            )
            send_telegram(status_msg)
            print(f"[{now_str}] Da gui status Telegram")
            last_status_time = datetime.now()

        time.sleep(CHECK_SECS)

    except KeyboardInterrupt:
        print("\nBot dung.")
        break
    except Exception as e:
        print(f"[{now_str}] Loi: {e}")
        # Gửi cảnh báo lỗi về Telegram
        try:
            send_telegram(
                f"❌ <b>SMC Bot - Loi</b>\n\n"
                f"Chi tiet: {str(e)[:200]}\n"
                f"<i>{datetime.now().strftime('%d/%m/%Y %H:%M')} (GMT+7)</i>"
            )
        except: pass
        time.sleep(60)