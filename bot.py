"""
bot.py — Trái tim của SMC Bot. Chứa loop chính (Data Fetching + Signal Scan + Execution).
Đã được refactor tách thành các module chuyên biệt: config, utils, state, learning, position_mgmt, signals.
"""
import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Các cấu hình và biến toàn cục
from config import (
    SYMBOLS, SIGNAL_INTERVALS, SCALP_INTERVALS, SWING_INTERVALS,
    GRID_BOT_ENABLED, GRID_INTERVAL, GRID_MIN_CANDLES,
    INTERVAL, DATA_SOURCE, ORDER_NOTIONAL_USDT, RR, MIN_TP_PCT, MIN_SL_PCT,
    MIN_SIGNAL_QUALITY_SCORE, WAIT_LOG_INTERVAL_SECONDS, BINGX_API_KEY, BINGX_SECRET_KEY, READ_ONLY_MODE,
    LIQUIDITY_FOCUS_ENABLED, LIQUIDITY_WINDOWS_VN_RAW,
)

# Helpers
from utils import (
    now_vn, format_price, build_telegram_dedup_keys,
    normalize_tp_sl_by_entry, align_tp_sl_with_rr, enforce_tp_sl_safety,
    sanitize_tp_sl, is_entry_still_valid, is_signal_tradeable, calc_order_quantity, calc_rr_from_levels
)

# Client giao dịch API
from bingx_client import bing_client, has_api_credentials

# Notification formatters & telegram sender
from notifications import (
    send_telegram, format_startup_msg, format_signal_msg, format_status_msg,
    format_order_result_msg, format_pnl_msg, format_closed_positions_summary, build_entry_reason
)

# Phân tích On-chart
from indicators import add_indicators
from signals import (
    scan_signal, scan_swing_signal, scan_grid_signal,
    pick_best_signal, resolve_signal_engine
)

# Quản lý lệnh, trailing, breakeven
from position_mgmt import (
    calc_live_pnl, calc_live_pnl_pct, check_breakeven_condition, check_trailing_stop,
    decide_positions_to_close, sync_position_levels_from_exchange, should_notify_pnl_change,
    current_max_active_orders, is_high_liquidity_time, passes_quality_gate,
    passes_liquidity_focus, effective_signal_cooldown
)

# Persistence (Lưu trạng thái)
from state import (
    load_learning_state, save_learning_state,
    load_active_positions, save_active_positions
)
from learning import apply_learning_to_signal_v2, update_learning_state


# ==========================================
# 0. SERVER DUMMY CHỐNG RENDER SLEEP
# ==========================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"SMC Bot is running!")
    def log_message(self, format, *args):
        pass # Tắt log HTTP request

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True, name="HealthServer").start()


def is_trading_enabled():
    """
    Chỉ cho phép đặt/đóng lệnh khi đủ API key và không bật READ_ONLY_MODE.
    """
    if READ_ONLY_MODE:
        return False
    return has_api_credentials()


def extract_order_avg_price(order_result, fallback_price):
    try:
        avg_price = order_result.get("data", {}).get("order", {}).get("avgPrice")
        if avg_price is not None and float(avg_price) > 0:
            return float(avg_price)
    except Exception:
        pass
    return float(fallback_price)


def fetch_data(symbol, interval="15m", candles=500):
    try:
        return bing_client.get_klines(symbol=symbol, interval=interval, limit=candles)
    except Exception:
        return None

def mark_learning_dirty(meta_dict):
    meta_dict["dirty"] = True

def maybe_flush_learning_state(state, meta, force=False):
    import time
    now_ts = time.time()
    if not meta.get("dirty"):
        return
    last_save_ts = float(meta.get("last_save_ts", 0.0))
    if force or (now_ts - last_save_ts >= 600): # 10 phút lưu 1 lần
        save_learning_state(state)
        meta["dirty"] = False
        meta["last_save_ts"] = now_ts


# ==========================================
# BACKGROUND FETCH & MAIN LOOP
# ==========================================
_df_cache = {symbol: {} for symbol in SYMBOLS}
_lock = threading.Lock()

def _bg_fetcher_for_symbol(symbol):
    """Thread riêng per symbol: fetch độc lập, không bị delay bởi symbol khác."""
    while True:
        try:
            for tf in SIGNAL_INTERVALS:
                df = fetch_data(symbol, tf)
                if df is None:
                    continue
                df = add_indicators(df)
                if df is None or len(df) == 0 or "close" not in df.columns:
                    continue
                with _lock:
                    _df_cache[symbol][tf] = df
                print(f"[BG] Updated {symbol} {tf} | Close: {df['close'].iloc[-1]}")
        except Exception as e:
            print(f"[WARN] _bg_fetcher [{symbol}] exception: {e}")
        time.sleep(30)

for _sym in SYMBOLS:
    threading.Thread(target=_bg_fetcher_for_symbol, args=(_sym,), daemon=True, name=f"bg_{_sym}").start()

time.sleep(10) # Đợi dữ liệu lần đầu

vst_bal = bing_client.get_vst_balance() if has_api_credentials() else 0.0
learning_state = load_learning_state()
learning_meta = {"dirty": False, "last_save_ts": time.time()}

active_positions_by_symbol = load_active_positions(SYMBOLS)
order_seq_by_symbol = {symbol: 0 for symbol in SYMBOLS}

# Khôi phục order_seq từ labels đã lưu để tránh đánh số trùng
for symbol in SYMBOLS:
    for pos in active_positions_by_symbol.get(symbol, []):
        label = pos.get("label", "")
        try:
            seq_num = int(label.replace("LỆNH #", "").strip())
            if seq_num > order_seq_by_symbol[symbol]:
                order_seq_by_symbol[symbol] = seq_num
        except Exception:
            pass

# Bổ sung vị thế thực trên sàn nếu chưa có trong state
for symbol in SYMBOLS:
    exchange_position = bing_client.get_open_position(symbol) if is_trading_enabled() else None
    already_tracked = any(
        p.get("side") == (exchange_position or {}).get("side")
        for p in active_positions_by_symbol.get(symbol, [])
    )
    if exchange_position and not already_tracked:
        order_seq_by_symbol[symbol] += 1
        exchange_position["label"] = f"LỆNH #{order_seq_by_symbol[symbol]}"
        active_positions_by_symbol[symbol].append(exchange_position)
        print(f"[STARTUP] [{symbol}] Phát hiện vị thế trên sàn chưa track → thêm vào danh sách.")
        
    if is_trading_enabled() and (not active_positions_by_symbol[symbol]):
        from config import LEVERAGE
        bing_client.set_leverage(symbol, "LONG", LEVERAGE)
        bing_client.set_leverage(symbol, "SHORT", LEVERAGE)

from config import GRID_STEP_PCT
send_telegram(format_startup_msg(
    vst_bal, is_trading_enabled(), resolve_signal_engine(), SCALP_INTERVALS, SWING_INTERVALS, 
    GRID_BOT_ENABLED, GRID_INTERVAL, GRID_STEP_PCT, resolve_signal_engine(), SYMBOLS
))

if not is_trading_enabled():
    send_telegram(
        "ℹ️ <b>Bot đang chạy ở chế độ READ-ONLY</b>\n"
        "Sẽ phân tích và gửi tín hiệu, nhưng không tự động đặt/đóng lệnh.\n"
        "Để bật auto trade: cung cấp BINGX_API_KEY + BINGX_SECRET_KEY và tắt READ_ONLY_MODE."
    )

last_signal_key_by_symbol = {symbol: None for symbol in SYMBOLS}
last_status_notify_ts_by_symbol = {symbol: time.time() for symbol in SYMBOLS}
last_pnl_notified_pct_by_symbol = {symbol: {} for symbol in SYMBOLS}
closed_cycle_pnl_by_symbol = {symbol: 0.0 for symbol in SYMBOLS}
bootstrapped_signal_by_symbol = {symbol: False for symbol in SYMBOLS}
last_entry_ts_by_symbol = {symbol: {} for symbol in SYMBOLS}
last_skip_reason_by_symbol = {symbol: "Bot vừa khởi động, đang chờ tín hiệu hợp lệ đầu tiên." for symbol in SYMBOLS}
last_wait_log_ts_by_symbol = {symbol: 0.0 for symbol in SYMBOLS}
last_tp_sl_sync_ts_by_symbol = {symbol: 0.0 for symbol in SYMBOLS}
last_gsheet_active_sync_ts = 0.0

try:
    from gsheets import setup_dashboard
    setup_dashboard()
except Exception as e:
    print(f"[WARN] setup_dashboard failed: {e}")



while True:
    try:
        for symbol in SYMBOLS:
            with _lock:
                symbol_frames = dict(_df_cache.get(symbol) or {})
            if not symbol_frames:
                continue

            active_positions = active_positions_by_symbol[symbol]
            primary_df = symbol_frames.get(INTERVAL)
            if primary_df is None:
                primary_df = next(iter(symbol_frames.values()), None)
            if primary_df is None or len(primary_df) < 3:
                continue
                
            last_closed = primary_df.iloc[-2]
            candle_time = str(last_closed["datetime"])
            live_price = bing_client.get_last_price(symbol)
            if live_price is None:
                live_price = float(last_closed["close"])

            candidates = []
            for tf in SCALP_INTERVALS:
                df_tf = symbol_frames.get(tf)
                if df_tf is None or len(df_tf) < 3:
                    continue
                scalp_signal = scan_signal(df_tf, symbol_frames=symbol_frames)
                if scalp_signal:
                    scalp_signal = dict(scalp_signal)
                    scalp_signal["interval"] = tf
                    scalp_signal["strategy"] = "scalp"
                    scalp_signal = apply_learning_to_signal_v2(learning_state, symbol, scalp_signal)
                    candidates.append(scalp_signal)

            for tf in SWING_INTERVALS:
                df_tf = symbol_frames.get(tf)
                if df_tf is None or len(df_tf) < 3:
                    continue
                swing_signal = scan_swing_signal(df_tf, symbol_frames=symbol_frames)
                if swing_signal:
                    swing_signal = dict(swing_signal)
                    swing_signal["interval"] = tf
                    swing_signal = apply_learning_to_signal_v2(learning_state, symbol, swing_signal)
                    candidates.append(swing_signal)

            if GRID_BOT_ENABLED:
                grid_df = symbol_frames.get(GRID_INTERVAL)
                if grid_df is not None and len(grid_df) >= GRID_MIN_CANDLES:
                    grid_signal = scan_grid_signal(grid_df, symbol_frames=symbol_frames)
                    if grid_signal:
                        grid_signal = dict(grid_signal)
                        grid_signal["interval"] = GRID_INTERVAL
                        grid_signal = apply_learning_to_signal_v2(learning_state, symbol, grid_signal)
                        candidates.append(grid_signal)

            signal_eval_time = now_vn()
            signal = pick_best_signal(candidates, signal_eval_time)
            
            if signal:
                active_limit = current_max_active_orders(signal_eval_time)
                if signal.get("source", DATA_SOURCE) != "BINGX":
                    last_skip_reason_by_symbol[symbol] = f"Nguồn tín hiệu không hợp lệ: {signal.get('source')}"
                    print(f"[WARN] Bỏ qua tín hiệu do nguồn không phải BingX: {signal.get('source')}")
                    continue
                    
                quality_ok, quality_reason = passes_quality_gate(signal)
                if not quality_ok:
                    last_skip_reason_by_symbol[symbol] = quality_reason
                    print(f"[INFO] [{symbol}] Bỏ qua tín hiệu: {quality_reason}")
                    continue
                    
                tradeable, tradeable_reason = is_signal_tradeable(signal)
                if not tradeable:
                    last_skip_reason_by_symbol[symbol] = tradeable_reason
                    print(f"[INFO] [{symbol}] Bỏ qua tín hiệu: {tradeable_reason}")
                    continue
                    
                entry_ok, drift_limit_pct, drift_pct = is_entry_still_valid(signal, float(live_price))
                if not entry_ok:
                    last_skip_reason_by_symbol[symbol] = f"Giá lệch khỏi entry {drift_pct:.3f}% > ngưỡng {drift_limit_pct:.3f}%."
                    print(f"[INFO] [{symbol}] Bỏ qua: giá đã drift xa khỏi entry signal.")
                    continue
                    
                liquidity_ok, liquidity_reason = passes_liquidity_focus(signal, signal_eval_time)
                if not liquidity_ok:
                    last_skip_reason_by_symbol[symbol] = liquidity_reason
                    print(f"[INFO] [{symbol}] Bỏ qua tín hiệu: {liquidity_reason}")
                    continue
                    
                sig_key = f"{signal['side']}_{signal.get('strategy', 'scalp')}_{signal.get('interval', INTERVAL)}_{signal['candle_time']}"
                signal_bucket = f"{signal['side']}_{signal.get('strategy', 'scalp')}_{signal.get('interval', INTERVAL)}"
                
                now_ts = time.time()
                last_entry_ts = last_entry_ts_by_symbol[symbol].get(signal_bucket)
                signal_cooldown = effective_signal_cooldown(signal)
                if last_entry_ts and (now_ts - last_entry_ts < signal_cooldown):
                    remaining = int(signal_cooldown - (now_ts - last_entry_ts))
                    last_skip_reason_by_symbol[symbol] = f"Đang cooldown {signal_bucket}, còn {max(remaining, 0)}s."
                    continue
                    
                if not bootstrapped_signal_by_symbol[symbol]:
                    if not is_trading_enabled():
                        bootstrapped_signal_by_symbol[symbol] = True
                    else:
                        last_signal_key_by_symbol[symbol] = sig_key
                        bootstrapped_signal_by_symbol[symbol] = True
                        last_skip_reason_by_symbol[symbol] = "Bootstrap: bỏ qua tín hiệu đầu tiên sau khi restart."
                        continue

                if sig_key != last_signal_key_by_symbol[symbol]:
                    if not is_trading_enabled():
                        order_seq_by_symbol[symbol] += 1
                        order_label = f"LỆNH #{order_seq_by_symbol[symbol]}"
                        send_telegram(format_signal_msg(signal, symbol, order_label, vst_balance_text=f"{vst_bal:.1f}"))
                        send_telegram("🧪 <b>Bỏ qua đặt lệnh tự động</b>\nLý do: bot đang ở chế độ READ-ONLY.")
                        last_skip_reason_by_symbol[symbol] = "READ-ONLY mode: chỉ gửi tín hiệu, không đặt lệnh."
                        last_signal_key_by_symbol[symbol] = sig_key
                        continue

                    removable_positions = decide_positions_to_close(
                        active_positions, signal["side"], float(live_price), active_limit
                    )
                    for pos in removable_positions:
                        pnl_snapshot = calc_live_pnl(pos, float(live_price))
                        close_resp = bing_client.close_position_market(symbol, pos.get("side"), pos.get("quantity"))
                        close_ok = close_resp and close_resp.get("code") == 0
                        if close_ok:
                            active_positions_by_symbol[symbol] = [x for x in active_positions_by_symbol[symbol] if x.get("label") != pos.get("label")]
                            active_positions = active_positions_by_symbol[symbol]
                            save_active_positions(active_positions_by_symbol)
                            closed_cycle_pnl_by_symbol[symbol] += pnl_snapshot
                            update_learning_state(
                                learning_state, symbol, pos.get("strategy", "scalp"),
                                pos.get("interval", INTERVAL), pos.get("side"), pnl_snapshot
                            )
                            mark_learning_dirty(learning_meta)
                        send_telegram(
                            "🔄 <b>Điều chỉnh danh mục lệnh</b>\n"
                            f"Mã: <b>{symbol}</b>\n"
                            f"Đóng lệnh: <b>{pos.get('label')}</b> ({pos.get('side')})\n"
                            f"PnL: <b>{pnl_snapshot:+.2f} USDT</b> - Limit lệnh: tối đa {active_limit}"
                        )
                    if (not active_positions) and removable_positions:
                        send_telegram(format_closed_positions_summary(symbol, closed_cycle_pnl_by_symbol[symbol]))
                        closed_cycle_pnl_by_symbol[symbol] = 0.0

                    if len(active_positions) >= active_limit:
                        last_skip_reason_by_symbol[symbol] = f"Đạt giới hạn lệnh mở ({len(active_positions)}/{active_limit})."
                        last_signal_key_by_symbol[symbol] = sig_key
                        continue

                    order_seq_by_symbol[symbol] += 1
                    order_label = f"LỆNH #{order_seq_by_symbol[symbol]}"
                    send_telegram(format_signal_msg(signal, symbol, order_label, vst_balance_text=f"{vst_bal:.1f}"))
                    last_signal_key_by_symbol[symbol] = sig_key
                    last_price = float(live_price)

                    order_signal = dict(signal)
                    order_signal["tp"], order_signal["sl"], levels_changed = normalize_tp_sl_by_entry(
                        order_signal["side"], last_price, order_signal.get("tp"), order_signal.get("sl")
                    )
                    rr_aligned_tp, rr_aligned_sl, rr_changed = align_tp_sl_with_rr(
                        order_signal["side"], last_price, order_signal.get("tp"), order_signal.get("sl"), order_signal.get("rr")
                    )
                    order_signal["tp"], order_signal["sl"] = rr_aligned_tp, rr_aligned_sl
                    levels_changed = levels_changed or rr_changed
                    pre_safe_tp, pre_safe_sl = order_signal["tp"], order_signal["sl"]
                    order_signal["tp"], order_signal["sl"] = enforce_tp_sl_safety(
                        order_signal["side"], last_price, order_signal["tp"], order_signal["sl"], last_price
                    )
                    rr_final_tp, rr_final_sl, rr_final_changed = align_tp_sl_with_rr(
                        order_signal["side"], last_price, order_signal.get("tp"), order_signal.get("sl"), order_signal.get("rr")
                    )
                    order_signal["tp"], order_signal["sl"] = sanitize_tp_sl(
                        order_signal["side"], rr_final_tp, rr_final_sl, last_price
                    )
                    
                    effective_rr = calc_rr_from_levels(order_signal["side"], last_price, order_signal.get("tp"), order_signal.get("sl"))
                    if effective_rr is not None:
                        order_signal["rr"] = effective_rr

                    quantity = calc_order_quantity(last_price, ORDER_NOTIONAL_USDT)
                    order = bing_client.place_market_order(
                        symbol, "BUY" if signal['side'] == 'LONG' else "SELL", signal['side'], quantity,
                        order_signal['tp'], order_signal['sl']
                    )
                    print(f"[{symbol}] Order Result: {order}")
                    
                    if order and order.get("code") == 0:
                        last_skip_reason_by_symbol[symbol] = "Đã vào lệnh thành công."
                        last_entry_ts_by_symbol[symbol][signal_bucket] = time.time()
                        fill_price = extract_order_avg_price(order, last_price)
                        send_telegram(format_order_result_msg(order_signal, symbol, order, order_label, fill_price, vst_balance_text=f"{vst_bal:.1f}"))
                        
                        protection_result = bing_client.add_missing_tp_sl(symbol, signal["side"], order_signal.get("tp"), order_signal.get("sl"))
                        exchange_pos = (protection_result or {}).get("position")
                        
                        active_positions_by_symbol[symbol].append({
                            "label": order_label,
                            "side": signal["side"],
                            "strategy": signal.get("strategy", "scalp"),
                            "interval": signal.get("interval", INTERVAL),
                            "entry": fill_price,
                            "quantity": float(quantity),
                            "tp": exchange_pos.get("tp") if exchange_pos and exchange_pos.get("tp") is not None else order_signal.get("tp"),
                            "sl": exchange_pos.get("sl") if exchange_pos and exchange_pos.get("sl") is not None else order_signal.get("sl"),
                            "opened_at": now_vn()
                        })
                        save_active_positions(active_positions_by_symbol)
                        last_pnl_notified_pct_by_symbol[symbol][order_label] = None
                    else:
                        err_msg = (order or {}).get("msg", "Không rõ lỗi")
                        last_skip_reason_by_symbol[symbol] = f"Lỗi đặt lệnh: {err_msg}"

            elif (not active_positions) and (time.time() - last_status_notify_ts_by_symbol[symbol] >= 3600):
                active_limit = current_max_active_orders(now_vn())
                liquidity_note = " Ngoài KH thanh khoản mở." if LIQUIDITY_FOCUS_ENABLED and not is_high_liquidity_time(now_vn()) else ""
                wait_reason = f"Đang chờ ({', '.join(SIGNAL_INTERVALS)}). Lỗi/Skip: {last_skip_reason_by_symbol.get(symbol, 'N/A')}{liquidity_note}"
                send_telegram(format_status_msg(symbol, live_price, candle_time, SIGNAL_INTERVALS, wait_reason))
                last_status_notify_ts_by_symbol[symbol] = time.time()

            now_ts = time.time()
            if (now_ts - last_wait_log_ts_by_symbol[symbol]) >= max(10, WAIT_LOG_INTERVAL_SECONDS):
                last_wait_log_ts_by_symbol[symbol] = now_ts

            if active_positions:
                if is_trading_enabled():
                    exchange_pos = bing_client.get_open_position(symbol)
                    if not exchange_pos:
                        for pos in active_positions:
                            pnl_snapshot = calc_live_pnl(pos, float(live_price))
                            update_learning_state(learning_state, symbol, pos.get("strategy", "scalp"), pos.get("interval", INTERVAL), pos.get("side"), pnl_snapshot)
                            try:
                                from gsheets import export_trade_to_sheet
                                export_trade_to_sheet(pos, pnl_snapshot, float(live_price), symbol)
                            except Exception:
                                pass
                        mark_learning_dirty(learning_meta)
                        closed_cycle_pnl_by_symbol[symbol] += sum(calc_live_pnl(pos, float(live_price)) for pos in active_positions)
                        send_telegram(format_closed_positions_summary(symbol, closed_cycle_pnl_by_symbol[symbol]))
                        
                        active_positions_by_symbol[symbol] = []
                        save_active_positions(active_positions_by_symbol)
                        last_pnl_notified_pct_by_symbol[symbol] = {}
                        closed_cycle_pnl_by_symbol[symbol] = 0.0
                        continue
                    else:
                        active_positions_by_symbol[symbol] = [
                            sync_position_levels_from_exchange(pos, exchange_pos)
                            for pos in active_positions_by_symbol[symbol]
                        ]
                        active_positions = active_positions_by_symbol[symbol]

                tracked_labels = {pos.get("label") for pos in active_positions if pos.get("label")}
                last_pnl_notified_pct_by_symbol[symbol] = {
                    label: pct for label, pct in last_pnl_notified_pct_by_symbol[symbol].items() if label in tracked_labels
                }

                for pos in list(active_positions):
                    pos = check_breakeven_condition(pos, float(live_price), symbol)
                    pos = check_trailing_stop(pos, float(live_price), symbol) # TRẢI NGHIỆM TRAILING STOP
                    
                    for i, tracked in enumerate(active_positions_by_symbol[symbol]):
                        if tracked.get("label") == pos.get("label"):
                            active_positions_by_symbol[symbol][i] = pos
                            break
                            
                    active_positions = active_positions_by_symbol[symbol]
                    
                    now_ts = time.time()
                    if is_trading_enabled() and (pos.get("tp") is not None or pos.get("sl") is not None):
                        # Giảm tần suất gọi API check/update TP/SL để tránh Rate Limit (Error 109429)
                        if (now_ts - last_tp_sl_sync_ts_by_symbol.get(symbol, 0.0)) >= 120:
                            tp_on_exchange, sl_on_exchange = bing_client.get_position_protection_levels(symbol, pos["side"])
                            missing_tp, missing_sl = pos.get("tp") is not None and tp_on_exchange is None, pos.get("sl") is not None and sl_on_exchange is None
                            if missing_tp or missing_sl:
                                bing_client.add_missing_tp_sl(
                                    symbol, pos["side"],
                                    pos.get("tp") if missing_tp else None,
                                    pos.get("sl") if missing_sl else None,
                                )
                            last_tp_sl_sync_ts_by_symbol[symbol] = now_ts

                    label = pos.get("label", "")
                    pnl_pct = calc_live_pnl_pct(pos, float(live_price))
                    prev_notified_pct = last_pnl_notified_pct_by_symbol[symbol].get(label)
                    if should_notify_pnl_change(prev_notified_pct, pnl_pct, threshold=10.0):
                        n_pnl_pct = float(position_mgmt.calc_live_pnl(pos, float(live_price)) / max(1, position_mgmt.calc_position_notional_base(pos)) * 100.0) if 'position_mgmt' in globals() else 0.0
                        # Workaround due to missing calc_position_notional_base direct import
                        from position_mgmt import calc_position_notional_base
                        n_pnl_pct = float(calc_live_pnl(pos, float(live_price)) / max(1, calc_position_notional_base(pos)) * 100.0)
                        
                        send_telegram(f"📌 <b>{symbol}</b>\n" + format_pnl_msg(pos, float(live_price), calc_live_pnl(pos, float(live_price)), pnl_pct, n_pnl_pct))
                        last_pnl_notified_pct_by_symbol[symbol][label] = pnl_pct

            maybe_flush_learning_state(learning_state, learning_meta)

        # Định kỳ cập nhật trạng thái các lệnh lên Google Sheet 'Active_Positions' (ví dụ mỗi 5 phút)
        if is_trading_enabled() and (time.time() - last_gsheet_active_sync_ts >= 300):
            try:
                from gsheets import export_active_positions
                latest_prices = {s: float(bing_client.get_last_price(s) or 0) for s in SYMBOLS}
                export_active_positions(active_positions_by_symbol, latest_prices)
                last_gsheet_active_sync_ts = time.time()
            except Exception as e:
                pass

        time.sleep(10)
    except Exception as e:
        print(f"Lỗi Main Loop: {e}")
        maybe_flush_learning_state(learning_state, learning_meta, force=True)
        time.sleep(10)
