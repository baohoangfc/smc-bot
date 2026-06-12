"""
notifications.py — Tất cả logic gửi Telegram và format message.
"""
import time
import threading

from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, HTTP_SESSION, HTTP_TIMEOUT,
    TELEGRAM_COMMANDS_ENABLED, PUBLIC_BASE_URL,
    DATA_SOURCE, INTERVAL, SIGNAL_INTERVALS, TELEGRAM_DEDUP_WINDOW_SECONDS,
    SCALP_RR_TARGET, MARGIN_STANDARD, MARGIN_HIGH_QUALITY, LEVERAGE, RR,
)
from utils import (
    format_price, format_vn_time, now_vn, calc_rr_from_levels,
    format_rr_text, build_telegram_dedup_keys,
)

_telegram_recent_messages: dict = {}
_telegram_dedup_lock = threading.Lock()


TELEGRAM_COMMANDS = [
    {"command": "start", "description": "Khởi động và xem hướng dẫn dùng bot"},
    {"command": "help", "description": "Danh sách lệnh Telegram của SMC Bot"},
    {"command": "status", "description": "Xem trạng thái chạy, symbol và TF theo dõi"},
    {"command": "positions", "description": "Xem lệnh đang mở, PnL và thời gian giữ"},
    {"command": "orders", "description": "Alias nhanh của /positions"},
    {"command": "balance", "description": "Xem số dư VST hiện tại"},
    {"command": "dashboard", "description": "Mở dashboard read-only nếu đã cấu hình URL"},
]


def _telegram_api_post(method: str, payload: dict, timeout=None):
    if not TELEGRAM_TOKEN:
        return None
    try:
        resp = HTTP_SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}",
            json=payload,
            timeout=HTTP_TIMEOUT if timeout is None else timeout,
        )
        if not resp.ok:
            print(f"[WARN] Telegram {method} failed: {resp.status_code} {resp.text[:300]}")
            return None
        return resp.json()
    except Exception as e:
        print(f"[WARN] Telegram {method} exception: {e}")
        return None


def send_telegram_direct(msg: str, chat_id=None, reply_to_message_id=None):
    """Send an operational Telegram reply without the signal-notification dedup cache."""
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN or not target_chat_id:
        return None
    payload = {"chat_id": target_chat_id, "text": msg, "parse_mode": "HTML"}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
        payload["allow_sending_without_reply"] = True
    return _telegram_api_post("sendMessage", payload)


def register_telegram_commands():
    """Publish BotFather-style command menu so users can see commands in Telegram."""
    if not TELEGRAM_TOKEN:
        return False

    payload = {"commands": TELEGRAM_COMMANDS}
    default_ok = bool((_telegram_api_post("setMyCommands", payload) or {}).get("ok"))
    chat_ok = False
    if TELEGRAM_CHAT_ID:
        chat_payload = dict(payload)
        chat_payload["scope"] = {"type": "chat", "chat_id": TELEGRAM_CHAT_ID}
        chat_ok = bool((_telegram_api_post("setMyCommands", chat_payload) or {}).get("ok"))
    if default_ok or chat_ok:
        print("[SYSTEM] Telegram command menu registered.")
        return True
    print("[WARN] Telegram command menu registration did not succeed.")
    return False


def format_help_command_msg():
    if PUBLIC_BASE_URL:
        dashboard_line = "\n🌐 /dashboard - mở dashboard read-only"
    else:
        dashboard_line = "\n🌐 /dashboard - URL dashboard chưa cấu hình PUBLIC_BASE_URL"
    return (
        "🤖 <b>SMC Bot - Lệnh Telegram</b>\n\n"
        "🚀 /start - kiểm tra bot và xem hướng dẫn nhanh\n"
        "❓ /help - xem danh sách lệnh\n"
        "📊 /status - trạng thái bot, mode, symbol và TF\n"
        "📌 /positions - vị thế/lệnh đang theo dõi, PnL và thời gian giữ\n"
        "📎 /orders - alias nhanh của /positions\n"
        "💵 /balance - số dư VST hiện tại"
        f"{dashboard_line}\n\n"
        "Nếu menu lệnh chưa hiện ngay, hãy đóng/mở lại khung chat Telegram hoặc gõ /help trực tiếp."
    )


def _normalize_telegram_command(text: str):
    first = (text or "").strip().split(maxsplit=1)[0].lower()
    if not first.startswith("/"):
        return ""
    command = first[1:].split("@", 1)[0]
    return command


def _is_allowed_command_chat(chat_id) -> bool:
    if not TELEGRAM_CHAT_ID:
        return True
    return str(chat_id) == str(TELEGRAM_CHAT_ID)


def run_telegram_command_polling(status_provider):
    """Small getUpdates loop for /status-like commands; safe to run in a daemon thread."""
    if not TELEGRAM_COMMANDS_ENABLED or not TELEGRAM_TOKEN:
        return

    register_telegram_commands()
    offset = None
    initial = _telegram_api_post(
        "getUpdates",
        {"timeout": 0, "limit": 1, "offset": -1, "allowed_updates": ["message"]},
        timeout=5,
    )
    if initial and initial.get("ok") and initial.get("result"):
        offset = max(int(item.get("update_id", 0)) for item in initial["result"]) + 1

    print("[SYSTEM] Telegram command polling started.")
    while True:
        params = {"timeout": 25, "limit": 20, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        data = _telegram_api_post("getUpdates", params, timeout=35)
        if not data or not data.get("ok"):
            time.sleep(5)
            continue

        for update in data.get("result", []):
            offset = int(update.get("update_id", 0)) + 1
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            text = message.get("text") or ""
            command = _normalize_telegram_command(text)
            if not command:
                continue
            if not _is_allowed_command_chat(chat_id):
                print(f"[WARN] Ignore Telegram command from unauthorized chat_id={chat_id}")
                continue

            try:
                if command in {"start", "help"}:
                    reply = format_help_command_msg()
                elif command in {"status", "positions", "orders", "balance", "dashboard"}:
                    reply = status_provider(command)
                else:
                    reply = "⚠️ Lệnh chưa hỗ trợ. Gõ /help để xem danh sách lệnh hiện có."
            except Exception as e:
                print(f"[WARN] Telegram command handler failed: {e}")
                reply = "⚠️ Bot gặp lỗi khi xử lý lệnh. Vui lòng thử lại sau."

            send_telegram_direct(reply, chat_id=chat_id, reply_to_message_id=message.get("message_id"))

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
    quality_tier = signal.get("quality_tier", "standard")
    decision_tfs = signal.get("decision_tfs") or []
    conflict_tfs = signal.get("decision_conflict_tfs") or []
    trend_tfs = signal.get("trend_tfs") or []
    trend_conflict_tfs = signal.get("trend_conflict_tfs") or []
    decision_text = ""
    if decision_tfs or conflict_tfs:
        aligned_text = ",".join(decision_tfs) if decision_tfs else "-"
        conflict_text = ",".join(conflict_tfs) if conflict_tfs else "-"
        decision_text = f" | TF quyết định +:{aligned_text} -:{conflict_text}"
    trend_text = ""
    if trend_tfs or trend_conflict_tfs:
        aligned_text = ",".join(trend_tfs) if trend_tfs else "-"
        conflict_text = ",".join(trend_conflict_tfs) if trend_conflict_tfs else "-"
        trend_score = signal.get("trend_score")
        score_text = f" ({float(trend_score):+.2f})" if trend_score is not None else ""
        trend_text = f" | Trend +:{aligned_text} -:{conflict_text}{score_text}"
    return f"{strategy.upper()} {tf} | mode={mode} | tier={quality_tier} | quality={quality_text} | RR={rr_text}{decision_text}{trend_text}"


def format_startup_msg(vst_balance, is_trading_enabled, engine_used,
                       scalp_intervals, swing_intervals, grid_enabled, grid_interval, grid_step_pct,
                       signal_engine_config, symbols, decision_intervals=None):
    mode_text = "READ-ONLY (chỉ gửi tín hiệu)" if not is_trading_enabled else "TRADE TỰ ĐỘNG"
    decision_intervals = decision_intervals or []
    decision_line = f"🔎 TF quyết định: <b>{', '.join(decision_intervals)}</b> (không đặt lệnh trực tiếp)\n" if decision_intervals else ""
    return (
        "🚀 <b>SMC Bot đã khởi động</b>\n"
        f"💵 Số dư: <b>{vst_balance:.4f} VST</b>\n"
        f"🧭 Chế độ: <b>{mode_text}</b>\n"
        f"📚 Danh mục: <b>{', '.join(symbols)}</b>\n"
        f"⏱️ Scalp TF: <b>{', '.join(scalp_intervals)}</b>\n"
        f"📈 Swing TF: <b>{', '.join(swing_intervals)}</b>\n"
        f"{decision_line}"
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


def format_status_msg(symbol, last_price, candle_time, tracked_tfs, wait_reason=None, next_update_hours=2):
    from datetime import timedelta
    next_time   = now_vn() + timedelta(hours=max(1, int(next_update_hours)))
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
        f"{pnl_emoji} <b>Theo dõi lệnh: báo khi ROI biến động ±20%</b>\n\n"
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


def format_eod_daily_pnl_msg(report_date: str, trades: int, net_pnl: float):
    emoji = "🟢" if net_pnl >= 0 else "🔴"
    return (
        f"{emoji} <b>Tổng kết PnL ngày {report_date}</b>\n"
        f"📦 Số lệnh đã đóng: <b>{int(trades)}</b>\n"
        f"💵 PnL ròng trong ngày: <b>{float(net_pnl):+.2f} USDT</b>\n"
        f"⏰ <b>{now_vn().strftime('%d/%m/%Y %H:%M')} (GMT+7)</b>"
    )


def format_eod_all_days_pnl_msg(summary: dict):
    total_pnl = float(summary.get("total_pnl", 0.0))
    emoji = "🟢" if total_pnl >= 0 else "🔴"
    best_day = summary.get("best_day") or {}
    worst_day = summary.get("worst_day") or {}

    best_text = "N/A"
    if best_day:
        best_text = f"{best_day.get('date')} ({float(best_day.get('net_pnl', 0.0)):+.2f} USDT)"

    worst_text = "N/A"
    if worst_day:
        worst_text = f"{worst_day.get('date')} ({float(worst_day.get('net_pnl', 0.0)):+.2f} USDT)"

    return (
        f"{emoji} <b>Tổng hợp PnL tất cả ngày đã tracking</b>\n"
        f"📅 Giai đoạn: <b>{summary.get('start_date', 'N/A')} → {summary.get('end_date', 'N/A')}</b>\n"
        f"🧮 Số ngày có giao dịch: <b>{int(summary.get('total_days', 0))}</b>\n"
        f"📦 Tổng số lệnh: <b>{int(summary.get('total_trades', 0))}</b>\n"
        f"💵 Tổng PnL lũy kế: <b>{total_pnl:+.2f} USDT</b>\n"
        f"🟢 Ngày lãi: <b>{int(summary.get('positive_days', 0))}</b> | "
        f"🔴 Ngày lỗ: <b>{int(summary.get('negative_days', 0))}</b> | "
        f"⚪ Hòa vốn: <b>{int(summary.get('flat_days', 0))}</b>\n"
        f"🏆 Ngày tốt nhất: <b>{best_text}</b>\n"
        f"🧯 Ngày tệ nhất: <b>{worst_text}</b>\n"
        f"⏰ <b>{now_vn().strftime('%d/%m/%Y %H:%M')} (GMT+7)</b>"
    )
