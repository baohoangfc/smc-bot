import requests
import pandas as pd
import numpy as np
import time
import os
from datetime import datetime, timedelta

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==========================================
# MINI HTTP SERVER - giữ Railway không sleep
# ==========================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - SMC Bot dang chay")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, format, *args): pass

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# ==========================================
# CONFIG
# ==========================================
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
INTERVAL         = os.environ.get("INTERVAL", "15m")
CHECK_SECS       = int(os.environ.get("CHECK_SECS", "60"))  # check mỗi 60s, lọc phút sau
RR               = float(os.environ.get("RR", "2.0"))

# Phút hợp lệ để gửi noti (đuôi 0 hoặc 5)
VALID_MINUTES = set(range(0, 60, 5))  # 0,5,10,15,...,55

def now_vn():
    """Giờ Việt Nam GMT+7"""
    return datetime.utcnow() + timedelta(hours=7)

def is_valid_minute():
    """Chỉ chạy khi phút hiện tại có đuôi 0 hoặc 5"""
    return now_vn().minute in VALID_MINUTES

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
    yf_map = {
        "1m":"1m","3m":"2m","5m":"5m","15m":"15m",
        "30m":"30m","1h":"60m","4h":"60m"
    }
    yf_interval = yf_map.get(interval, "15m")
    period_map = {
        "1m":"7d","2m":"60d","5m":"60d","15m":"60d",
        "30m":"60d","60m":"730d"
    }
    period = period_map.get(yf_interval, "60d")

    url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
    params = {"interval": yf_interval, "range": period, "events": "history"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    try:
        r   = requests.get(url, params=params, headers=headers, timeout=15)
        raw = r.json()
        result = raw.get("chart", {}).get("result", [])
        if not result:
            print("Yahoo tra ve rong")
            return None
        ts    = result[0]["timestamp"]
        ohlcv = result[0]["indicators"]["quote"][0]
        df = pd.DataFrame({
            "datetime": pd.to_datetime(ts, unit="s") + pd.Timedelta(hours=7),
            "open":  ohlcv["open"],
            "high":  ohlcv["high"],
            "low":   ohlcv["low"],
            "close": ohlcv["close"],
            "volume":ohlcv["volume"]
        })
        df = df.dropna().reset_index(drop=True)
        df = df.tail(candles).reset_index(drop=True)
        print(f"Data OK: {len(df)} nen | GC=F")
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
            body=abs(c_-o); rng=h_-l_
            if c_<o and rng>0 and body/rng>0.35 and body>atr*0.25:
                if not any(df['close'].iloc[k]<l_ for k in range(j+1,i)):
                    return {'type':'BULL_OB','hi':h_,'lo':l_,'mid':(h_+l_)/2}
    elif sig == "BEAR":
        for j in range(i-1, start-1, -1):
            o,c_,h_,l_ = df['open'].iloc[j],df['close'].iloc[j],df['high'].iloc[j],df['low'].iloc[j]
            body=abs(c_-o); rng=h_-l_
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
# BACKTEST TRONG NGÀY
# ==========================================
def run_daily_backtest(df, target_date=None):
    """
    Chạy backtest SMC cho 1 ngày cụ thể (mặc định hôm nay GMT+7)
    Trả về list các lệnh đã xử lý trong ngày đó
    """
    if target_date is None:
        target_date = now_vn().date()

    sh = swing_highs(df, n=3)
    sl = swing_lows(df,  n=3)

    trades      = []
    lenh_mo     = None
    ob_pending  = None
    ob_ttl      = 0
    RR_BT       = RR
    MAX_HOLD    = 32

    for i in range(10, len(df)):
        row_date = pd.to_datetime(df['datetime'].iloc[i]).date()

        c = df['close'].iloc[i]
        h = df['high'].iloc[i]
        l = df['low'].iloc[i]

        # --- Quản lý lệnh đang mở ---
        if lenh_mo is not None:
            lenh_mo['held'] += 1

            if lenh_mo['held'] >= MAX_HOLD:
                pnl = 1 if (c > lenh_mo['entry'] and lenh_mo['type']=='LONG') or \
                           (c < lenh_mo['entry'] and lenh_mo['type']=='SHORT') else -1
                lenh_mo.update({'result':'TIMEOUT','pnl_r': round(pnl*0.3,1), 'exit_time': str(df['datetime'].iloc[i])})
                if row_date == target_date or lenh_mo['trade_date'] == target_date:
                    trades.append(dict(lenh_mo))
                lenh_mo = None
                continue

            if lenh_mo['type'] == 'LONG':
                if h >= lenh_mo['entry']+(lenh_mo['entry']-lenh_mo['sl']) and not lenh_mo.get('be'):
                    lenh_mo['sl'] = lenh_mo['entry']+1; lenh_mo['be']=True
                if l <= lenh_mo['sl']:
                    pnl_r = 0.0 if lenh_mo.get('be') else -1.0
                    lenh_mo.update({'result':'BE' if lenh_mo.get('be') else 'LOSS','pnl_r':pnl_r,'exit_time':str(df['datetime'].iloc[i])})
                    if lenh_mo['trade_date'] == target_date:
                        trades.append(dict(lenh_mo))
                    lenh_mo = None
                elif h >= lenh_mo['tp']:
                    lenh_mo.update({'result':'WIN','pnl_r':RR_BT,'exit_time':str(df['datetime'].iloc[i])})
                    if lenh_mo['trade_date'] == target_date:
                        trades.append(dict(lenh_mo))
                    lenh_mo = None

            elif lenh_mo['type'] == 'SHORT':
                if l <= lenh_mo['entry']-(lenh_mo['sl']-lenh_mo['entry']) and not lenh_mo.get('be'):
                    lenh_mo['sl'] = lenh_mo['entry']-1; lenh_mo['be']=True
                if h >= lenh_mo['sl']:
                    pnl_r = 0.0 if lenh_mo.get('be') else -1.0
                    lenh_mo.update({'result':'BE' if lenh_mo.get('be') else 'LOSS','pnl_r':pnl_r,'exit_time':str(df['datetime'].iloc[i])})
                    if lenh_mo['trade_date'] == target_date:
                        trades.append(dict(lenh_mo))
                    lenh_mo = None
                elif l <= lenh_mo['tp']:
                    lenh_mo.update({'result':'WIN','pnl_r':RR_BT,'exit_time':str(df['datetime'].iloc[i])})
                    if lenh_mo['trade_date'] == target_date:
                        trades.append(dict(lenh_mo))
                    lenh_mo = None
            continue

        # --- Tìm CHoCH + OB ---
        sig = detect_structure(df, sh, sl, i)
        if sig:
            ob = find_ob(df, sig, i, lookback=20)
            if ob:
                ob_pending = ob
                ob_ttl = 0

        if ob_pending:
            ob_ttl += 1
            if ob_ttl > 40:
                ob_pending = None

        # --- Vào lệnh ---
        if ob_pending and lenh_mo is None:
            ob = ob_pending
            if ob['type'] == 'BULL_OB' and valid_long(df, i, ob) and bull_confirm(df, i):
                e, sl_, tp = sltp_long(df, i, ob)
                if e and (e - sl_) > 2:
                    lenh_mo = {
                        'type':'LONG','entry':round(e,2),'sl':round(sl_,2),'tp':round(tp,2),
                        'entry_time':str(df['datetime'].iloc[i]),
                        'trade_date': pd.to_datetime(df['datetime'].iloc[i]).date(),
                        'held':0,'be':False
                    }
                    ob_pending = None
            elif ob['type'] == 'BEAR_OB' and valid_short(df, i, ob) and bear_confirm(df, i):
                e, sl_, tp = sltp_short(df, i, ob)
                if e and (sl_ - e) > 2:
                    lenh_mo = {
                        'type':'SHORT','entry':round(e,2),'sl':round(sl_,2),'tp':round(tp,2),
                        'entry_time':str(df['datetime'].iloc[i]),
                        'trade_date': pd.to_datetime(df['datetime'].iloc[i]).date(),
                        'held':0,'be':False
                    }
                    ob_pending = None

    return trades


def format_daily_backtest_msg(trades, target_date):
    ngay = target_date.strftime('%d/%m/%Y')
    gio  = now_vn().strftime('%H:%M')

    if not trades:
        return (
            f"📋 <b>Backtest trong ngày {ngay}</b>\n\n"
            f"Không có lệnh nào được kích hoạt hôm nay.\n"
            f"<i>⏰ Cập nhật lúc {gio} (GMT+7)</i>"
        )

    thang  = [t for t in trades if t.get('result') == 'WIN']
    thua   = [t for t in trades if t.get('result') == 'LOSS']
    be     = [t for t in trades if t.get('result') == 'BE']
    total_r = sum(t.get('pnl_r', 0) for t in trades)
    winrate = round(len(thang) / len(trades) * 100) if trades else 0

    icon_total = "🟢" if total_r > 0 else ("🔴" if total_r < 0 else "⚪")
    lines = [
        f"📋 <b>Backtest trong ngày {ngay}</b>\n",
        f"Tổng lệnh : <b>{len(trades)}</b>  |  Thắng: <b>{len(thang)}</b>  Thua: <b>{len(thua)}</b>  BE: <b>{len(be)}</b>",
        f"Winrate   : <b>{winrate}%</b>",
        f"Tổng R    : {icon_total} <b>{'+' if total_r>0 else ''}{total_r}R</b>\n",
        "─────────────────────",
    ]

    for idx, t in enumerate(trades, 1):
        res    = t.get('result', '?')
        emoji  = "✅" if res=='WIN' else ("❌" if res=='LOSS' else ("⚪" if res=='BE' else "⏱"))
        side   = "MUA" if t['type']=='LONG' else "BÁN"
        # Lấy giờ vào lệnh
        try:
            gio_vao = pd.to_datetime(t['entry_time']).strftime('%H:%M')
        except:
            gio_vao = '--:--'
        try:
            gio_ra = pd.to_datetime(t.get('exit_time','')).strftime('%H:%M')
        except:
            gio_ra = '--:--'

        pnl_r = t.get('pnl_r', 0)
        pnl_str = f"+{pnl_r}R" if pnl_r > 0 else (f"{pnl_r}R" if pnl_r < 0 else "0R (BE)")

        lines.append(
            f"{emoji} <b>Lệnh {idx}: {side}</b>  [{gio_vao} → {gio_ra}]\n"
            f"   Vào: <b>{t['entry']}</b>  SL: <b>{t['sl']}</b>  TP: <b>{t['tp']}</b>\n"
            f"   Kết quả: <b>{res}</b>  ({pnl_str})"
        )

    lines.append(f"\n<i>⏰ Cập nhật lúc {gio} (GMT+7)</i>")
    lines.append(f"<i>⚠️ Chỉ tham khảo, không phải lệnh thật</i>")
    return "\n".join(lines)


# ==========================================
# FORMAT TIN NHẮN TIẾNG VIỆT
# ==========================================
def format_signal_msg(signal, price, tf):
    side   = signal['side']
    emoji  = "🟢" if side == "LONG" else "🔴"
    lenh   = "MUA (LONG)" if side == "LONG" else "BÁN (SHORT)"
    entry  = round(signal['entry'], 2)
    sl     = round(signal['sl'], 2)
    tp     = round(signal['tp'], 2)
    rr_r   = round(abs(tp - entry) / max(abs(entry - sl), 0.01), 2)
    gio    = now_vn().strftime('%d/%m/%Y %H:%M')
    return (
        f"{emoji} <b>TÍN HIỆU SMC - VÀNG {tf}</b>\n\n"
        f"📌 Lệnh      : <b>{lenh}</b>\n"
        f"💰 Giá hiện tại : <b>{price}</b>\n"
        f"🎯 Vào lệnh  : <b>{entry}</b>\n"
        f"🛑 Cắt lỗ    : <b>{sl}</b>\n"
        f"✅ Chốt lời  : <b>{tp}</b>\n"
        f"📊 R:R       : <b>1:{rr_r}</b>\n\n"
        f"<i>⏰ {gio} (GMT+7)</i>\n"
        f"<i>⚠️ Chỉ tham khảo, tự xác nhận trước khi vào lệnh</i>"
    )

def format_status_msg(price, tf, signal, next_time_str):
    gio = now_vn().strftime('%H:%M')
    if signal:
        side  = signal['side']
        lenh  = "MUA (LONG)" if side == "LONG" else "BÁN (SHORT)"
        emoji = "🟢" if side == "LONG" else "🔴"
        sig_info = (
            f"{emoji} Có tín hiệu: <b>{lenh}</b>\n"
            f"   Vào lệnh : <b>{round(signal['entry'],2)}</b>\n"
            f"   Cắt lỗ   : <b>{round(signal['sl'],2)}</b>\n"
            f"   Chốt lời : <b>{round(signal['tp'],2)}</b>\n"
            f"   (Đang chờ retest vùng OB)"
        )
    else:
        sig_info = "⏳ Chưa có tín hiệu. Đang theo dõi..."

    return (
        f"🤖 <b>SMC Bot - Cập nhật {gio} (GMT+7)</b>\n\n"
        f"Giá VÀNG   : <b>{price}</b>\n"
        f"Khung TG   : <b>{tf}</b>\n"
        f"Trạng thái : ✅ Đang chạy\n\n"
        f"{sig_info}\n\n"
        f"<i>Cập nhật tiếp theo lúc {next_time_str}</i>"
    )

def format_startup_msg(tf, check_min):
    gio = now_vn().strftime('%d/%m/%Y %H:%M')
    return (
        f"🚀 <b>Bot SMC VÀNG {tf} đã khởi động</b>\n\n"
        f"✅ Đang chạy bình thường\n"
        f"🔄 Cập nhật mỗi {check_min} phút (các phút :00, :05, :10...)\n"
        f"⏰ {gio} (GMT+7)"
    )

def format_error_msg(err):
    gio = now_vn().strftime('%d/%m/%Y %H:%M')
    return (
        f"❌ <b>SMC Bot - Lỗi</b>\n\n"
        f"Chi tiết: {str(err)[:200]}\n"
        f"<i>⏰ {gio} (GMT+7)</i>"
    )

def format_nodata_msg():
    gio = now_vn().strftime('%d/%m/%Y %H:%M')
    return (
        f"⚠️ <b>SMC Bot - Cảnh báo</b>\n\n"
        f"Không tải được dữ liệu VÀNG\n"
        f"Đang thử lại...\n\n"
        f"<i>⏰ {gio} (GMT+7)</i>"
    )

# ==========================================
# MAIN LOOP
# ==========================================
print(f"Bot SMC khoi dong | {INTERVAL} | Chi ban phut :00,:05,:10,...")
send_telegram(format_startup_msg(INTERVAL, 5))

last_signal_key      = None
last_status_time     = now_vn() - timedelta(minutes=10)
STATUS_INTERVAL      = 10 * 60
last_checked_min     = -1
last_backtest_date   = None   # ngày đã gửi báo cáo backtest
BACKTEST_HOUR        = 20     # gửi lúc 20:00 GMT+7 mỗi ngày

while True:
    try:
        vn_now  = now_vn()
        now_str = vn_now.strftime('%H:%M:%S')

        cur_min = vn_now.minute

        # ⏰ BỘ LỌC THỜI GIAN: chỉ chạy khi phút có đuôi 0 hoặc 5
        if not is_valid_minute():
            print(f"[{now_str}] Bo qua (phut :{cur_min:02d})")
            time.sleep(20)
            continue

        # Tránh chạy lặp 2 lần trong cùng 1 phút
        if cur_min == last_checked_min:
            time.sleep(20)
            continue
        last_checked_min = cur_min

        df = fetch_data(INTERVAL, candles=500)

        if df is None:
            print(f"[{now_str}] Khong tai duoc data")
            elapsed = (now_vn() - last_status_time).total_seconds()
            if elapsed >= STATUS_INTERVAL:
                send_telegram(format_nodata_msg())
                last_status_time = now_vn()
            time.sleep(60)
            continue

        df     = add_indicators(df)
        price  = round(df['close'].iloc[-1], 2)
        signal = scan_signal(df)

        # --- GỬI TÍN HIỆU MỚI ---
        if signal:
            sig_key = f"{signal['side']}_{signal['candle_time']}"
            if sig_key != last_signal_key:
                send_telegram(format_signal_msg(signal, price, INTERVAL))
                print(f"[{now_str}] TIN HIEU {signal['side']} | Entry:{round(signal['entry'],2)} SL:{round(signal['sl'],2)} TP:{round(signal['tp'],2)}")
                last_signal_key = sig_key
            else:
                print(f"[{now_str}] Gia:{price} | Tin hieu cu ({signal['side']}) - bo qua")
        else:
            print(f"[{now_str}] Gia:{price} | Chua co tin hieu")

        # --- GỬI TRẠNG THÁI MỖI 10 PHÚT ---
        elapsed = (now_vn() - last_status_time).total_seconds()
        if elapsed >= STATUS_INTERVAL:
            next_str = (now_vn() + timedelta(minutes=10)).strftime('%H:%M')
            send_telegram(format_status_msg(price, INTERVAL, signal, next_str))
            print(f"[{now_str}] Da gui status Telegram")
            last_status_time = now_vn()

        # --- GỬI BACKTEST TRONG NGÀY lúc 20:00 GMT+7 ---
        today = vn_now.date()
        if vn_now.hour == BACKTEST_HOUR and last_backtest_date != today:
            print(f"[{now_str}] Dang chay backtest trong ngay {today}...")
            bt_trades = run_daily_backtest(df, target_date=today)
            bt_msg    = format_daily_backtest_msg(bt_trades, today)
            send_telegram(bt_msg)
            last_backtest_date = today
            print(f"[{now_str}] Da gui backtest {today}: {len(bt_trades)} lenh")

        # Ngủ 30s rồi check lại (đảm bảo không bỏ sót phút :x5/:x0)
        time.sleep(30)

    except KeyboardInterrupt:
        print("\nBot dung.")
        break
    except Exception as e:
        print(f"[{now_vn().strftime('%H:%M:%S')}] Loi: {e}")
        try:
            send_telegram(format_error_msg(e))
        except: pass
        time.sleep(60)