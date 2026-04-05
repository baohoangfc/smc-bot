"""
notifications.py — Tất cả logic gửi Telegram và format message.
"""
import time
import threading

from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, HTTP_SESSION, HTTP_TIMEOUT,
    DATA_SOURCE, INTERVAL, SIGNAL_INTERVALS, TELEGRAM_DEDUP_WINDOW_SECONDS,
    SCALP_RR_TARGET, MARGIN_STANDARD, MARGIN_HIGH_QUALITY, LEVERAGE, RR,
)
from utils import (
    format_price, format_vn_time, now_vn, calc_rr_from_levels,
    format_rr_text, build_telegram_dedup_keys,
)

_telegram_recent_messages: dict = {}
_telegram_dedup_lock = threading.Lock()


def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    now_ts   = time.time()
    msg_keys = build_telegram_dedup_keys(msg)
    with _telegram_dedup_lock:
        expired_keys = [k for k, ts in _telegram_recent_messages.items()
                        if now_ts - ts > TELEGRAM_DEDUP_WINDOW_SECONDS]
        for key in expired_keys:
            _telegram_recent_messages.pop(key, None)
        for key in msg_keys:
            last_sent_ts = _telegram_recent_messages.get(key)
            if last_sent_ts is not None and (now_ts - last_sent_ts) <= TELEGRAM_DEDUP_WINDOW_SECONDS:
                print("[INFO] Skip duplicate Telegram message within dedup window.")
                return
        for key in msg_keys:
            _telegram_recent_messages[key] = now_ts
    try:
        HTTP_SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=HTTP_TIMEOUT,
        )
    except Exception as e:
        print(f"[WARN] send_telegram exception: {e}")


# ────────────────────────────────────────────────────────────
# Format helpers
# ────────────────────────────────────────────────────────────

def build_entry_reason(signal):
    strategy      = signal.get("strategy", "scalp")
    tf            = signal.get("interval", INTERVAL)
    mode          = signal.get("signal_mode", "strict")
    quality_score = signal.get("quality_score")
    quality_text  = f"{float(quality_score):.2f}" if quality_score is not None else "N/A"
    rr_text       = format_rr_text(
        signal["side"], signal.get("entry"), signal.get("tp"), signal.get("sl"),
        fallback_rr=signal.get("rr", SCALP_RR_TARGET), decimals=2
    )
    return f"{strategy.upper()} {tf} | mode={mode} | quality={quality_text} | RR={rr_text}"


def format_startup_msg(vst_balance, is_trading_enabled, engine_used,
                       scalp_intervals, swing_intervals, grid_enabled, grid_interval, grid_step_pct,
                       signal_engine_config, symbols):
    mode_text = "READ-ONLY (chỉ gửi tín hiệu)" if not is_trading_enabled else "TRADE TỰ ĐỘNG"
    return (
        "🚀 <b>SMC Bot đã khởi động</b>\n"
        f"💵 Số dư: <b>{vst_balance:.4f} VST</b>\n"
        f"🧭 Chế độ: <b>{mode_text}</b>\n"
        f"📚 Danh mục: <b>{', '.join(symbols)}</b>\n"
        f"⏱️ Scalp TF: <b>{', '.join(scalp_intervals)}</b>\n"
        f"📈 Swing TF: <b>{', '.join(swing_intervals)}</b>\n"
        f"🧱 Grid fast: <b>{'ON' if grid_enabled else 'OFF'}</b> ({grid_interval}, step={grid_step_pct:.2f}%)\n"
        f"🧠 Signal engine: <b>{engine_used}</b> (config={signal_engine_config})\n"
        f"🕒 Thời gian: <b>{now_vn().strftime('%d/%m/%Y %H:%M')} (GMT+7)</b>"
    )


def format_signal_msg(signal, symbol, order_label=None, vst_balance_text="N/A"):
    emoji      = "🟢" if signal["side"] == "LONG" else "🔴"
    side_text  = "MUA (LONG)" if signal["side"] == "LONG" else "BÁN (SHORT)"
    rr_text    = format_rr_text(
        signal["side"], signal.get("entry"), signal.get("tp"), signal.get("sl"),
        fallback_rr=signal.get("rr", SCALP_RR_TARGET), decimals=1
    )
    signal_mode   = signal.get("signal_mode", "strict")
    quality_score = signal.get("quality_score")
    quality_text  = f"{float(quality_score):.2f}" if quality_score is not None else "N/A"
    tf            = signal.get("interval", INTERVAL)
    strategy      = signal.get("strategy", "scalp")
    order_line    = f"🆔 Mã lệnh  : <b>{order_label}</b>\n" if order_label else ""
    signal_source = signal.get("source", DATA_SOURCE)
    entry_reason  = build_entry_reason(signal)
    return (
        f"{emoji} <b>TÍN HIỆU SMC - {symbol} {tf}</b>\n\n"
        f"{order_line}"
        f"📌 Lệnh      : <b>{side_text}</b>\n"
        f"🧩 Chiến lược: <b>{strategy}</b>\n"
        f"💰 Giá hiện tại : <b>{format_price(signal['entry'])}</b>\n"
        f"🎯 Vào lệnh  : <b>{format_price(signal['entry'])}</b>\n"
        f"🛑 Cắt lỗ    : <b>{format_price(signal['sl'])}</b>\n"
        f"✅ Chốt lời  : <b>{format_price(signal['tp'])}</b>\n"
        f"📊 R:R       : <b>{rr_text}</b>\n"
        f"⭐ Quality   : <b>{quality_text}</b>\n"
        f"🧠 Mode      : <b>{signal_mode}</b>\n\n"
        f"📝 Lý do vào lệnh: <b>{entry_reason}</b>\n"
        f"💵 Số dư VST : <b>{vst_balance_text}</b>\n"
        f"🔌 Nguồn dữ liệu: <b>{signal_source}</b>\n"
        f"⏰ <b>{format_vn_time(signal['candle_time'])} (GMT+7)</b>\n"
        "⚠️ <i>Chỉ tham khảo, tự xác nhận trước khi vào lệnh</i>"
    )


def format_status_msg(symbol, last_price, candle_time, tracked_tfs, wait_reason=None):
    from datetime import timedelta
    next_time   = now_vn() + timedelta(hours=1)
    reason_text = wait_reason or "Chưa có setup đạt điều kiện vào lệnh ở các khung đang theo dõi."
    if len(reason_text) > 420:
        reason_text = reason_text[:417].rstrip() + "..."
    tracked_tfs_text = ", ".join(tracked_tfs) if tracked_tfs else INTERVAL
    return (
        f"🤖 <b>SMC Bot - Cập nhật {format_vn_time(candle_time, '%H:%M')} (GMT+7)</b>\n\n"
        f"Giá {symbol} : <b>{format_price(last_price)}</b>\n"
        f"Khung TG    : <b>{INTERVAL}</b>\n"
        f"TF theo dõi : <b>{tracked_tfs_text}</b>\n"
        f"Nguồn dữ liệu: <b>{DATA_SOURCE}</b>\n"
        "Trạng thái  : ✅ <b>Đang chạy</b>\n\n"
        "⏳ <b>Chưa có setup SMC đạt chuẩn, bot vẫn đang theo dõi...</b>\n\n"
        f"📝 Lý do chờ: <b>{reason_text}</b>\n\n"
        f"Cập nhật tiếp theo lúc <b>{format_vn_time(next_time, '%H:%M')}</b>"
    )


def format_order_result_msg(signal, symbol, order_result, order_label=None, filled_entry=None, vst_balance_text="N/A"):
    order_id      = (order_result or {}).get("data", {}).get("order", {}).get("orderId", "N/A")
    entry_to_show = filled_entry if filled_entry is not None else signal.get("entry")
    rr_text       = format_rr_text(
        signal["side"], entry_to_show, signal.get("tp"), signal.get("sl"),
        fallback_rr=signal.get("rr"), decimals=2
    )
    order_line = f"🆔 Mã lệnh  : <b>{order_label}</b>\n" if order_label else ""
    # Xác định margin hiển thị (nếu có trong signal)
    used_margin = signal.get("margin", MARGIN_STANDARD)
    return (
        "🟢 <b>DEMO - Đặt lệnh thị trường</b>\n\n"
        f"🏷️ Mã        : <b>{symbol}</b>\n"
        f"{order_line}"
        f"📌 Lệnh     : <b>{'MUA (LONG)' if signal['side'] == 'LONG' else 'BÁN (SHORT)'}</b>\n"
        f"🎯 Entry    : <b>{format_price(entry_to_show)}</b>\n"
        f"🛑 Cắt lỗ   : <b>{format_price(signal['sl'])}</b>\n"
        f"✅ Chốt lời : <b>{format_price(signal['tp'])}</b>\n"
        f"📊 R:R      : <b>{rr_text}</b>\n"
        f"💵 Ký quỹ   : <b>${used_margin:.1f}</b>\n"
        f"📦 Notional : <b>$({used_margin * LEVERAGE:.0f})</b>\n"
        f"⚙️ Leverage  : <b>x{LEVERAGE}</b>\n"
        f"⏰ Thời gian : <b>{now_vn().strftime('%d/%m %H:%M')} (GMT+7)</b>"
    )


def format_pnl_msg(position, last_price, pnl, pnl_pct, notional_pnl_pct):
    side       = position["side"]
    qty        = float(position.get("quantity", 0) or 0)
    entry      = float(position.get("entry", 0) or 0)
    pnl_emoji  = "🟢" if pnl >= 0 else "🔴"
    tp_val = position.get("tp")
    sl_val = position.get("sl")
    tp_text = format_price(tp_val) if tp_val is not None else "<i>(Đang đồng bộ...)</i>"
    sl_text = format_price(sl_val) if sl_val is not None else "<i>(Đang đồng bộ...)</i>"
    
    rr_text = "N/A"
    if tp_val is not None and sl_val is not None:
        rr_text = format_rr_text(side, entry, tp_val, sl_val, fallback_rr=position.get("rr"), decimals=2)
    
    order_label = position.get("label", "LỆNH")
    return (
        f"{pnl_emoji} <b>Theo dõi lệnh: báo khi ROI biến động ±10%</b>\n\n"
        f"🆔 Mã lệnh  : <b>{order_label}</b>\n"
        f"📌 Lệnh      : <b>{'MUA (LONG)' if side == 'LONG' else 'BÁN (SHORT)'}</b>\n"
        f"🎯 Entry     : <b>{format_price(entry)}</b>\n"
        f"🛑 Cắt lỗ    : <b>{sl_text}</b>\n"
        f"✅ Chốt lời  : <b>{tp_text}</b>\n"
        f"📊 R:R       : <b>{rr_text}</b>\n"
        f"💰 Giá hiện tại: <b>{format_price(last_price)}</b>\n"
        f"📦 Khối lượng : <b>{qty}</b>\n"
        f"💵 PnL tạm tính: <b>{pnl:+.2f} USDT</b>\n"
        f"📈 ROI ký quỹ: <b>{pnl_pct:+.2f}%</b> | PnL/notional: <b>{notional_pnl_pct:+.2f}%</b>\n"
        f"⏰ <b>{now_vn().strftime('%d/%m/%Y %H:%M')} (GMT+7)</b>"
    )


def format_closed_positions_summary(symbol, total_pnl):
    emoji = "🟢" if total_pnl >= 0 else "🔴"
    return (
        f"{emoji} <b>{symbol}: Đã đóng hết lệnh đang theo dõi</b>\n"
        f"💵 Tổng PnL đã đóng: <b>{total_pnl:+.2f} USDT</b>\n"
        f"⏰ <b>{now_vn().strftime('%d/%m/%Y %H:%M')} (GMT+7)</b>"
    )
