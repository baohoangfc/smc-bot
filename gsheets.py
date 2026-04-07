import os
import json
from datetime import datetime
from collections import defaultdict

try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    _GSPREAD_AVAILABLE = True
except ImportError:
    _GSPREAD_AVAILABLE = False

from config import GOOGLE_SHEETS_CREDENTIALS_JSON, GOOGLE_SHEET_ID
from utils import now_vn

_client = None
_spreadsheet = None

def _get_spreadsheet():
    global _client, _spreadsheet
    if not _GSPREAD_AVAILABLE or not GOOGLE_SHEETS_CREDENTIALS_JSON or not GOOGLE_SHEET_ID:
        return None
        
    if _spreadsheet is not None:
        return _spreadsheet
        
    try:
        cred_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS_JSON)
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(cred_dict, scope) # type: ignore
        _client = gspread.authorize(creds)
        _spreadsheet = _client.open_by_key(GOOGLE_SHEET_ID)
        print("[GSHEETS] Khởi tạo kết nối Google Sheet thành công.")
        return _spreadsheet
    except Exception as e:
        print(f"[GSHEETS] Khởi tạo thất bại: {e}")
        return None

def _get_or_create_worksheet(title, rows="1000", cols="20"):
    sp = _get_spreadsheet()
    if sp is None:
        return None
    try:
        return sp.worksheet(title)
    except gspread.exceptions.WorksheetNotFound: # type: ignore
        try:
            return sp.add_worksheet(title=title, rows=rows, cols=cols)
        except Exception as e:
            print(f"[GSHEETS] Không thể tạo sheet {title}: {e}")
            return None
    except Exception as e:
        print(f"[GSHEETS] Lỗi lấy worksheet {title}: {e}")
        return None

def export_trade_to_sheet(position: dict, pnl: float, close_price: float, symbol: str):
    sp = _get_spreadsheet()
    if sp is None:
        return
        
    try:
        ws = sp.sheet1
    except Exception:
        return

    try:
        from position_mgmt import calc_position_notional_base
        
        # Kiểm tra và cập nhật Header nếu sheet mới
        if len(ws.col_values(1)) == 0:
            headers = [
                "Time_Close", "Mã Lệnh", "Symbol", "Side", "Strategy", "Interval", 
                "Entry", "Close Price", "SL", "TP", "PnL", "ROI %", "Duration (Mins)",
                "Result", "Quality Score", "SMC Mode", "Expected RR"
            ]
            ws.insert_row(headers, 1)

        close_time = now_vn()
        open_time_dt = position.get("opened_at", close_time)
        
        duration_mins = "N/A"
        try:
            diff = close_time - open_time_dt
            duration_mins = round(diff.total_seconds() / 60, 2)
        except:
            pass

        time_str = close_time.strftime("%Y-%m-%d %H:%M:%S")
        label = position.get("label", "Unknown")
        side = position.get("side", "")
        strategy = position.get("strategy", "")
        interval = position.get("interval", "")
        
        entry = float(position.get("entry", 0) or 0)
        sl = float(position.get("sl", 0) or 0)
        tp = float(position.get("tp", 0) or 0)
        
        # Metadata
        quality = position.get("quality_score", "")
        mode = position.get("signal_mode", "")
        expected_rr = position.get("rr", "")
        
        # ROI %
        notional_base = calc_position_notional_base(position)
        leverage = float(position.get("leverage", 100))
        margin_base = max(notional_base / leverage, 0.0001)
        roi_pct = round((pnl / margin_base) * 100, 2)
        
        # Phân loại Result
        result = "BREAKEVEN"
        if pnl > margin_base * 0.05: # Thắng lớn hơn 5% ROI
            result = "WIN"
        elif pnl < -margin_base * 0.05: # Thua trên 5% ROI
            result = "LOSS"
        
        row_data = [
            time_str,
            label,
            symbol,
            side,
            strategy,
            interval,
            entry,
            close_price,
            sl,
            tp,
            round(pnl, 4),
            f"{roi_pct}%",
            duration_mins,
            result,
            quality,
            mode,
            expected_rr
        ]
        
        ws.append_row(row_data)
        print(f"[GSHEETS] Đã đồng bộ lệnh {label} ({symbol}) lên Google Sheet.")
        appended_ok = append_trade_and_pnl_history_row(row_data)
        if not appended_ok:
            rebuild_trade_and_pnl_history()
        # Refresh sheet tổng hợp lợi nhuận ngay sau khi thêm lệnh đóng.
        update_profit_summary_sheet()
    except Exception as e:
        print(f"[WARN] export_trade_to_sheet lỗi: {e}")


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        if isinstance(value, str):
            cleaned = value.replace("%", "").replace(",", "").strip()
            return float(cleaned) if cleaned else default
        return float(value)
    except Exception:
        return default


def _safe_profit_factor(gross_profit: float, gross_loss: float) -> float:
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / abs(gross_loss)


def _compute_streaks(trades: list[dict]) -> tuple[int, int]:
    max_win = 0
    max_loss = 0
    cur_win = 0
    cur_loss = 0
    for t in trades:
        pnl = t.get("pnl", 0.0)
        if pnl > 0:
            cur_win += 1
            cur_loss = 0
            max_win = max(max_win, cur_win)
        elif pnl < 0:
            cur_loss += 1
            cur_win = 0
            max_loss = max(max_loss, cur_loss)
        else:
            cur_win = 0
            cur_loss = 0
    return max_win, max_loss


def append_trade_and_pnl_history_row(history_row: list):
    """
    Append 1 giao dịch mới vào Trade_History + PnL_History (nhanh hơn full rebuild).
    Trả về False để caller fallback rebuild khi dữ liệu hiện tại bị lệch/thiếu.
    """
    sp = _get_spreadsheet()
    if sp is None:
        return False

    trade_ws = _get_or_create_worksheet("Trade_History", rows="5000", cols="20")
    pnl_ws = _get_or_create_worksheet("PnL_History", rows="5000", cols="12")
    if trade_ws is None or pnl_ws is None:
        return False

    try:
        trade_values = trade_ws.get_all_values()
        pnl_values = pnl_ws.get_all_values()

        trade_headers = [
            "Trade #", "Time_Close", "Mã Lệnh", "Symbol", "Side", "Strategy", "Interval",
            "Entry", "Close Price", "SL", "TP", "PnL", "ROI %", "Duration (Mins)",
            "Result", "Quality Score", "SMC Mode", "Expected RR"
        ]
        pnl_headers = [
            "Trade #", "Time_Close", "PnL", "Cumulative PnL", "Rolling Win Rate (%)",
            "Wins", "Losses", "Breakeven"
        ]

        if not trade_values:
            trade_ws.update([trade_headers], "A1")
            trade_count = 0
        else:
            trade_count = max(len(trade_values) - 1, 0)

        if not pnl_values:
            pnl_ws.update([pnl_headers], "A1")
            prev_cum_pnl = 0.0
            wins = losses = be = 0
            trade_count = 0
        else:
            last = pnl_values[-1]
            # Nếu sheet đang chỉ có header
            if len(pnl_values) == 1:
                prev_cum_pnl = 0.0
                wins = losses = be = 0
                trade_count = 0
            else:
                prev_cum_pnl = _to_float(last[3] if len(last) > 3 else 0.0, 0.0)
                wins = int(_to_float(last[5] if len(last) > 5 else 0.0, 0.0))
                losses = int(_to_float(last[6] if len(last) > 6 else 0.0, 0.0))
                be = int(_to_float(last[7] if len(last) > 7 else 0.0, 0.0))
                trade_count = int(_to_float(last[0] if len(last) > 0 else 0.0, trade_count))

        trade_no = trade_count + 1
        pnl = _to_float(history_row[10], 0.0)
        cumulative_pnl = prev_cum_pnl + pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        else:
            be += 1
        win_rate = (wins / trade_no) * 100.0

        trade_ws.append_row([trade_no] + history_row)
        pnl_ws.append_row([
            trade_no,
            history_row[0],
            round(pnl, 6),
            round(cumulative_pnl, 6),
            round(win_rate, 4),
            wins,
            losses,
            be,
        ])
        return True
    except Exception as e:
        print(f"[WARN] append_trade_and_pnl_history_row lỗi: {e}")
        return False


def rebuild_trade_and_pnl_history(history_sheet_name: str = "Sheet1",
                                  trade_history_sheet_name: str = "Trade_History",
                                  pnl_history_sheet_name: str = "PnL_History"):
    """
    Rebuild lịch sử giao dịch + lịch sử PnL lũy kế từ toàn bộ lệnh đã đóng (Sheet1).
    Mục tiêu: khi bot restart hoặc dashboard lệch dữ liệu, có thể tính lại from scratch.
    """
    sp = _get_spreadsheet()
    if sp is None:
        return

    try:
        history_ws = sp.worksheet(history_sheet_name)
    except Exception as e:
        print(f"[GSHEETS] Không tìm thấy sheet lịch sử '{history_sheet_name}': {e}")
        return

    trade_ws = _get_or_create_worksheet(trade_history_sheet_name, rows="5000", cols="20")
    pnl_ws = _get_or_create_worksheet(pnl_history_sheet_name, rows="5000", cols="12")
    if trade_ws is None or pnl_ws is None:
        return

    try:
        rows = history_ws.get_all_records()
        if not rows:
            trade_ws.clear()
            pnl_ws.clear()
            trade_ws.update([["Status"], ["Chưa có dữ liệu trong Sheet1"]], "A1")
            pnl_ws.update([["Status"], ["Chưa có dữ liệu trong Sheet1"]], "A1")
            return

        trades = []
        for idx, r in enumerate(rows, start=1):
            pnl = _to_float(r.get("PnL"), 0.0)
            close_time_raw = str(r.get("Time_Close", "") or "").strip()
            dt = None
            if close_time_raw:
                try:
                    dt = datetime.fromisoformat(close_time_raw)
                except Exception:
                    try:
                        dt = datetime.strptime(close_time_raw, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        dt = None

            trades.append({
                "row_idx": idx,
                "time_raw": close_time_raw,
                "time": dt,
                "label": r.get("Mã Lệnh", ""),
                "symbol": r.get("Symbol", ""),
                "side": r.get("Side", ""),
                "strategy": r.get("Strategy", ""),
                "interval": r.get("Interval", ""),
                "entry": _to_float(r.get("Entry"), 0.0),
                "close_price": _to_float(r.get("Close Price"), 0.0),
                "sl": _to_float(r.get("SL"), 0.0),
                "tp": _to_float(r.get("TP"), 0.0),
                "pnl": pnl,
                "roi_pct": _to_float(r.get("ROI %"), 0.0),
                "duration_mins": _to_float(r.get("Duration (Mins)"), 0.0),
                "result": str(r.get("Result", "") or ""),
                "quality": r.get("Quality Score", ""),
                "signal_mode": r.get("SMC Mode", ""),
                "expected_rr": r.get("Expected RR", ""),
            })

        trades.sort(key=lambda x: (x["time"] is None, x["time"] or datetime.max, x["row_idx"]))

        trade_rows = [[
            "Trade #", "Time_Close", "Mã Lệnh", "Symbol", "Side", "Strategy", "Interval",
            "Entry", "Close Price", "SL", "TP", "PnL", "ROI %", "Duration (Mins)",
            "Result", "Quality Score", "SMC Mode", "Expected RR"
        ]]
        pnl_rows = [[
            "Trade #", "Time_Close", "PnL", "Cumulative PnL", "Rolling Win Rate (%)",
            "Wins", "Losses", "Breakeven"
        ]]

        cumulative_pnl = 0.0
        wins = 0
        losses = 0
        be = 0
        for i, t in enumerate(trades, start=1):
            pnl = float(t["pnl"])
            cumulative_pnl += pnl
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            else:
                be += 1
            win_rate = (wins / i) * 100.0

            trade_rows.append([
                i,
                t["time_raw"],
                t["label"],
                t["symbol"],
                t["side"],
                t["strategy"],
                t["interval"],
                t["entry"],
                t["close_price"],
                t["sl"],
                t["tp"],
                round(pnl, 6),
                f"{t['roi_pct']:.4f}%",
                t["duration_mins"],
                t["result"],
                t["quality"],
                t["signal_mode"],
                t["expected_rr"],
            ])
            pnl_rows.append([
                i,
                t["time_raw"],
                round(pnl, 6),
                round(cumulative_pnl, 6),
                round(win_rate, 4),
                wins,
                losses,
                be,
            ])

        trade_ws.clear()
        pnl_ws.clear()
        trade_ws.update(trade_rows, "A1")
        pnl_ws.update(pnl_rows, "A1")
        print(f"[GSHEETS] Rebuilt Trade_History ({len(trades)} dòng) và PnL_History từ Sheet1.")
    except Exception as e:
        print(f"[WARN] rebuild_trade_and_pnl_history lỗi: {e}")


def update_profit_summary_sheet(history_sheet_name: str = "Sheet1", summary_sheet_name: str = "Profit_Summary"):
    """
    Đọc lịch sử lệnh đóng từ Sheet1 và ghi thống kê lợi nhuận vào Profit_Summary.
    Không cần file CSV local.
    """
    sp = _get_spreadsheet()
    if sp is None:
        return

    try:
        history_ws = sp.worksheet(history_sheet_name)
    except Exception as e:
        print(f"[GSHEETS] Không tìm thấy sheet lịch sử '{history_sheet_name}': {e}")
        return

    summary_ws = _get_or_create_worksheet(summary_sheet_name, rows="2000", cols="12")
    if summary_ws is None:
        return

    try:
        rows = history_ws.get_all_records()
        if not rows:
            summary_ws.clear()
            summary_ws.update([
                ["SMC BOT PROFIT SUMMARY", ""],
                ["Status", "Chưa có dữ liệu lệnh đóng trong Sheet1"]
            ], "A1")
            return

        trades = []
        for r in rows:
            pnl = _to_float(r.get("PnL"), 0.0)
            time_raw = str(r.get("Time_Close", "") or "").strip()
            dt = None
            if time_raw:
                try:
                    dt = datetime.fromisoformat(time_raw)
                except Exception:
                    try:
                        dt = datetime.strptime(time_raw, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        dt = None
            trades.append({"pnl": pnl, "time": dt})

        total_trades = len(trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        losses = sum(1 for t in trades if t["pnl"] < 0)
        breakeven = total_trades - wins - losses
        gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gross_loss = sum(t["pnl"] for t in trades if t["pnl"] < 0)
        net_pnl = sum(t["pnl"] for t in trades)
        avg_pnl = (net_pnl / total_trades) if total_trades else 0.0
        avg_win = (gross_profit / wins) if wins else 0.0
        avg_loss = (gross_loss / losses) if losses else 0.0
        payoff_ratio = (avg_win / abs(avg_loss)) if avg_loss < 0 else 0.0
        expectancy = avg_pnl
        win_rate = (wins / total_trades * 100.0) if total_trades else 0.0
        profit_factor = _safe_profit_factor(gross_profit, gross_loss)
        best_trade = max((t["pnl"] for t in trades), default=0.0)
        worst_trade = min((t["pnl"] for t in trades), default=0.0)
        max_win_streak, max_loss_streak = _compute_streaks(trades)

        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for t in trades:
            equity += t["pnl"]
            peak = max(peak, equity)
            dd = equity - peak
            if dd < max_drawdown:
                max_drawdown = dd

        by_day = defaultdict(list)
        by_week = defaultdict(list)
        for t in trades:
            if t["time"] is None:
                continue
            day_key = t["time"].date().isoformat()
            iso = t["time"].isocalendar()
            week_key = f"{iso.year}-W{iso.week:02d}"
            by_day[day_key].append(t["pnl"])
            by_week[week_key].append(t["pnl"])

        def _aggregate(bucket: dict):
            out = []
            for k in sorted(bucket.keys()):
                values = bucket[k]
                n = len(values)
                pnl_sum = sum(values)
                win_cnt = sum(1 for v in values if v > 0)
                out.append((k, n, pnl_sum, pnl_sum / n if n else 0.0, (win_cnt / n * 100.0) if n else 0.0))
            return out

        daily_rows = _aggregate(by_day)
        weekly_rows = _aggregate(by_week)

        pf_text = "inf" if profit_factor == float("inf") else f"{profit_factor:.4f}"
        summary_rows = [
            ["SMC BOT PROFIT SUMMARY", ""],
            ["Last Updated", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")],
            ["Total trades", total_trades],
            ["Wins/Loss/BE", f"{wins}/{losses}/{breakeven}"],
            ["Win rate (%)", round(win_rate, 4)],
            ["Gross profit", round(gross_profit, 6)],
            ["Gross loss", round(gross_loss, 6)],
            ["Net PnL", round(net_pnl, 6)],
            ["Avg PnL/trade", round(avg_pnl, 6)],
            ["Avg WIN", round(avg_win, 6)],
            ["Avg LOSS", round(avg_loss, 6)],
            ["Payoff ratio", round(payoff_ratio, 4)],
            ["Expectancy", round(expectancy, 6)],
            ["Profit factor", pf_text],
            ["Best trade", round(best_trade, 6)],
            ["Worst trade", round(worst_trade, 6)],
            ["Max drawdown", round(max_drawdown, 6)],
            ["Max win streak", max_win_streak],
            ["Max loss streak", max_loss_streak],
            [],
            ["Daily summary (30 ngày gần nhất)", "", "", "", ""],
            ["date", "trades", "net_pnl", "avg_pnl", "win_rate_%"],
        ]
        for d, n, pnl_sum, avg, wr in daily_rows[-30:]:
            summary_rows.append([d, n, round(pnl_sum, 6), round(avg, 6), round(wr, 3)])

        summary_rows += [[], ["Weekly summary (16 tuần gần nhất)", "", "", "", ""], ["week", "trades", "net_pnl", "avg_pnl", "win_rate_%"]]
        for w, n, pnl_sum, avg, wr in weekly_rows[-16:]:
            summary_rows.append([w, n, round(pnl_sum, 6), round(avg, 6), round(wr, 3)])

        summary_ws.clear()
        summary_ws.update(summary_rows, "A1")

        daily_header_idx = 20
        daily_start_idx = daily_header_idx + 1
        daily_end_idx = daily_start_idx + len(daily_rows[-30:])
        weekly_header_idx = daily_end_idx + 1
        weekly_start_idx = weekly_header_idx + 1
        weekly_end_idx = weekly_start_idx + len(weekly_rows[-16:])

        fmt_requests = [
            {
                "repeatCell": {
                    "range": {
                        "sheetId": summary_ws.id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 5,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.13, "green": 0.42, "blue": 0.30},
                            "textFormat": {
                                "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                                "bold": True,
                                "fontSize": 12,
                            },
                            "horizontalAlignment": "CENTER",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": summary_ws.id,
                        "startRowIndex": 20,
                        "endRowIndex": 22,
                        "startColumnIndex": 0,
                        "endColumnIndex": 5,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.93, "green": 0.95, "blue": 1.0},
                            "textFormat": {"bold": True},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": summary_ws.id,
                        "startRowIndex": weekly_header_idx,
                        "endRowIndex": weekly_header_idx + 2,
                        "startColumnIndex": 0,
                        "endColumnIndex": 5,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.96, "green": 0.94, "blue": 0.88},
                            "textFormat": {"bold": True},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            },
            {
                "autoResizeDimensions": {
                    "dimensions": {
                        "sheetId": summary_ws.id,
                        "dimension": "COLUMNS",
                        "startIndex": 0,
                        "endIndex": 5,
                    }
                }
            },
        ]
        sp.batch_update({"requests": fmt_requests})

        print(f"[GSHEETS] Đã cập nhật sheet {summary_sheet_name} ({total_trades} trades).")
    except Exception as e:
        print(f"[WARN] update_profit_summary_sheet lỗi: {e}")

def export_active_positions(active_positions_by_symbol: dict, latest_prices: dict):
    """
    Cập nhật danh sách các lệnh đang chạy sang sheet 'Active_Positions'.
    Cập nhật đè lên để phản ánh trạng thái realtime.
    """
    ws = _get_or_create_worksheet("Active_Positions")
    if ws is None:
        return
        
    try:
        from position_mgmt import calc_live_pnl_pct, calc_live_pnl
        rows = [
            ["Symbol", "Mã Lệnh", "Side", "Mode", "Entry", "Live Price", "SL", "TP", "PnL", "ROI %", "Time Opened"]
        ]
        
        for symbol, positions in active_positions_by_symbol.items():
            live_price = float(latest_prices.get(symbol, 0))
            for pos in positions:
                label = pos.get("label", "")
                side = pos.get("side", "")
                mode = pos.get("signal_mode", "")
                entry = pos.get("entry", 0)
                sl = pos.get("sl", 0)
                tp = pos.get("tp", 0)
                time_opened = pos.get("opened_at")
                time_str = time_opened.strftime("%Y-%m-%d %H:%M:%S") if hasattr(time_opened, "strftime") else str(time_opened)
                
                pnl = 0.0
                roi_pct = 0.0
                if live_price > 0:
                    pnl = calc_live_pnl(pos, live_price)
                    roi_pct = calc_live_pnl_pct(pos, live_price)
                    
                rows.append([
                    symbol, label, side, mode, entry, live_price, sl, tp, 
                    round(pnl, 4), f"{round(roi_pct, 2)}%", time_str
                ])
                
        ws.clear()
        if len(rows) > 0:
            ws.update(rows, 'A1')
            
    except Exception as e:
        print(f"[WARN] export_active_positions lỗi: {e}")

def setup_dashboard():
    """
    Tạo hoặc đảm bảo Sheet Dashboard tồn tại với bố cục đẹp hơn và có biểu đồ trực quan.
    """
    ws = _get_or_create_worksheet("Dashboard", rows="180", cols="10")
    if ws is None:
        return

    try:
        data = [
            ["📈 SMC BOT DASHBOARD", "", "", "", "", "", "", "", "", ""],
            ["Last Sync", '=IFERROR(TEXT(MAX(Sheet1!A2:A),"yyyy-mm-dd hh:mm:ss"), "N/A")', "", "Timezone", "UTC", "", "", "", "", ""],
            ["", "", "", "", "", "", "", "", "", ""],
            ["📊 Tổng quan", "Giá trị", "", "⚡ Hiệu suất", "Giá trị", "", "🛡️ Rủi ro", "Giá trị", "", ""],
            ["Tổng lệnh", '=COUNTA(Sheet1!B2:B)', "", "Win rate", '=IF(B5=0,0,COUNTIF(Sheet1!N2:N,"WIN")/B5)', "", "Profit factor", '=IF(ABS(SUMIF(Sheet1!K2:K,"<0"))=0,"inf",SUMIF(Sheet1!K2:K,">0")/ABS(SUMIF(Sheet1!K2:K,"<0")))', "", ""],
            ["WIN", '=COUNTIF(Sheet1!N2:N,"WIN")', "", "Avg PnL", '=IF(B5=0,0,SUM(Sheet1!K2:K)/B5)', "", "Max DD", '=IFERROR(MIN(ArrayFormula(SUMIF(ROW(Sheet1!K2:K),"<="&ROW(Sheet1!K2:K),Sheet1!K2:K)-MAX(FILTER(ArrayFormula(SUMIF(ROW(Sheet1!K2:K),"<="&ROW(Sheet1!K2:K),Sheet1!K2:K)),ROW(Sheet1!K2:K)<=ROW(Sheet1!K2:K)))),0)', "", ""],
            ["LOSS", '=COUNTIF(Sheet1!N2:N,"LOSS")', "", "Avg WIN", '=IF(B6=0,0,SUMIF(Sheet1!K2:K,">0")/B6)', "", "Best trade", '=IFERROR(MAX(Sheet1!K2:K),0)', "", ""],
            ["BREAKEVEN", '=COUNTIF(Sheet1!N2:N,"BREAKEVEN")', "", "Avg LOSS", '=IF(B7=0,0,SUMIF(Sheet1!K2:K,"<0")/B7)', "", "Worst trade", '=IFERROR(MIN(Sheet1!K2:K),0)', "", ""],
            ["Net PnL", '=SUM(Sheet1!K2:K)', "", "Payoff ratio", '=IF(ABS(E8)=0,0,E7/ABS(E8))', "", "", "", "", ""],
            ["", "", "", "", "", "", "", "", "", ""],
            ["📅 Daily PnL (30 ngày)", "PnL", "", "📅 Weekly PnL (16 tuần)", "PnL", "", "", "", "", ""],
            [
                '=IFERROR(TEXT(INDEX(SORT(UNIQUE(FILTER(IFERROR(DATEVALUE(LEFT(Sheet1!A2:A,10)),),Sheet1!A2:A<>"")),1,FALSE),SEQUENCE(30,1,1,1)),"yyyy-mm-dd"),"")',
                '=IF(A12="","",SUM(FILTER(Sheet1!K2:K,IFERROR(DATEVALUE(LEFT(Sheet1!A2:A,10)),)=A12)))',
                "",
                '=IFERROR(INDEX(SORT(UNIQUE(FILTER(TEXT(IFERROR(DATEVALUE(LEFT(Sheet1!A2:A,10)),),"yyyy")&"-W"&TEXT(ISOWEEKNUM(IFERROR(DATEVALUE(LEFT(Sheet1!A2:A,10)),)),"00"),Sheet1!A2:A<>"")),1,FALSE),SEQUENCE(16,1,1,1)),"")',
                '=IF(D12="", "", SUM(FILTER(Sheet1!K2:K, TEXT(IFERROR(DATEVALUE(LEFT(Sheet1!A2:A,10)),),"yyyy")&"-W"&TEXT(ISOWEEKNUM(IFERROR(DATEVALUE(LEFT(Sheet1!A2:A,10)),)),"00")=D12)))',
                "",
                "",
                "",
                "",
                "",
            ],
        ]

        ws.clear()
        ws.update(data, 'A1', value_input_option='USER_ENTERED')

        sp = _get_spreadsheet()
        if sp is None:
            return

        history_id = None
        try:
            history_id = sp.sheet1.id
        except Exception:
            history_id = None

        # Xóa chart cũ để tránh nhân bản mỗi lần refresh dashboard
        try:
            metadata = sp.fetch_sheet_metadata()
            embedded_objects = metadata.get("sheets", [{}])[0].get("charts", [])
            # fallback cho schema phổ biến: top-level embeddedObjects
            if not embedded_objects:
                embedded_objects = metadata.get("embeddedObjects", [])
            delete_requests = []
            for obj in embedded_objects:
                obj_id = obj.get("chartId") or obj.get("objectId")
                if obj_id is not None:
                    delete_requests.append({"deleteEmbeddedObject": {"objectId": obj_id}})
            if delete_requests:
                sp.batch_update({"requests": delete_requests})
        except Exception:
            pass

        fmt_requests = [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 4}},
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            {
                "mergeCells": {
                    "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 10},
                    "mergeType": "MERGE_ALL",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 10},
                    "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.07, "green": 0.2, "blue": 0.37}, "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True, "fontSize": 16}, "horizontalAlignment": "CENTER"}},
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": ws.id, "startRowIndex": 3, "endRowIndex": 4, "startColumnIndex": 0, "endColumnIndex": 8},
                    "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.87, "green": 0.93, "blue": 1}, "textFormat": {"bold": True}, "horizontalAlignment": "CENTER"}},
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": ws.id, "startRowIndex": 4, "endRowIndex": 9, "startColumnIndex": 0, "endColumnIndex": 8},
                    "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.98, "green": 0.99, "blue": 1.0}}},
                    "fields": "userEnteredFormat(backgroundColor)",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": ws.id, "startRowIndex": 10, "endRowIndex": 11, "startColumnIndex": 0, "endColumnIndex": 8},
                    "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.91, "green": 0.97, "blue": 0.9}, "textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": ws.id, "startRowIndex": 4, "endRowIndex": 9, "startColumnIndex": 4, "endColumnIndex": 5},
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": ws.id, "startRowIndex": 4, "endRowIndex": 9, "startColumnIndex": 1, "endColumnIndex": 2},
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0.0000"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": ws.id, "startRowIndex": 11, "endRowIndex": 42, "startColumnIndex": 1, "endColumnIndex": 2},
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0.0000"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": ws.id, "startRowIndex": 11, "endRowIndex": 28, "startColumnIndex": 4, "endColumnIndex": 5},
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0.0000"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{"sheetId": ws.id, "startRowIndex": 11, "endRowIndex": 42, "startColumnIndex": 1, "endColumnIndex": 2}],
                        "booleanRule": {
                            "condition": {"type": "NUMBER_GREATER", "values": [{"userEnteredValue": "0"}]},
                            "format": {"textFormat": {"foregroundColor": {"red": 0.1, "green": 0.5, "blue": 0.2}, "bold": True}},
                        },
                    },
                    "index": 0,
                }
            },
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{"sheetId": ws.id, "startRowIndex": 11, "endRowIndex": 42, "startColumnIndex": 1, "endColumnIndex": 2}],
                        "booleanRule": {
                            "condition": {"type": "NUMBER_LESS", "values": [{"userEnteredValue": "0"}]},
                            "format": {"textFormat": {"foregroundColor": {"red": 0.72, "green": 0.11, "blue": 0.11}, "bold": True}},
                        },
                    },
                    "index": 0,
                }
            },
            {
                "autoResizeDimensions": {
                    "dimensions": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 10}
                }
            },
        ]

        sp.batch_update({"requests": fmt_requests})

        chart_requests = [
            {
                "addChart": {
                    "chart": {
                        "spec": {
                            "title": "Tỷ lệ WIN/LOSS/BE",
                            "pieChart": {
                                "legendPosition": "RIGHT_LEGEND",
                                "domain": {"sourceRange": {"sources": [{"sheetId": ws.id, "startRowIndex": 5, "endRowIndex": 8, "startColumnIndex": 0, "endColumnIndex": 1}]}},
                                "series": {"sourceRange": {"sources": [{"sheetId": ws.id, "startRowIndex": 5, "endRowIndex": 8, "startColumnIndex": 1, "endColumnIndex": 2}]}}
                            }
                        },
                        "position": {"overlayPosition": {"anchorCell": {"sheetId": ws.id, "rowIndex": 2, "columnIndex": 8}, "widthPixels": 440, "heightPixels": 280}}
                    }
                }
            },
            {
                "addChart": {
                    "chart": {
                        "spec": {
                            "title": "Daily PnL (30 ngày)",
                            "basicChart": {
                                "chartType": "COLUMN",
                                "legendPosition": "NO_LEGEND",
                                "axis": [
                                    {"position": "BOTTOM_AXIS", "title": "Date"},
                                    {"position": "LEFT_AXIS", "title": "PnL"}
                                ],
                                "domains": [{"domain": {"sourceRange": {"sources": [{"sheetId": ws.id, "startRowIndex": 11, "endRowIndex": 42, "startColumnIndex": 0, "endColumnIndex": 1}]}}}],
                                "series": [{"series": {"sourceRange": {"sources": [{"sheetId": ws.id, "startRowIndex": 11, "endRowIndex": 42, "startColumnIndex": 1, "endColumnIndex": 2}]}}}],
                            }
                        },
                        "position": {"overlayPosition": {"anchorCell": {"sheetId": ws.id, "rowIndex": 12, "columnIndex": 6}, "widthPixels": 620, "heightPixels": 320}}
                    }
                }
            },
        ]

        if history_id is not None:
            chart_requests.append(
                {
                    "addChart": {
                        "chart": {
                            "spec": {
                                "title": "Lịch sử PnL theo lệnh",
                                "basicChart": {
                                    "chartType": "LINE",
                                    "legendPosition": "NO_LEGEND",
                                    "axis": [
                                        {"position": "BOTTOM_AXIS", "title": "Trade Index"},
                                        {"position": "LEFT_AXIS", "title": "PnL"}
                                    ],
                                    "series": [{
                                        "series": {"sourceRange": {"sources": [{"sheetId": history_id, "startRowIndex": 1, "startColumnIndex": 10, "endColumnIndex": 11}]}}
                                    }],
                                }
                            },
                            "position": {"overlayPosition": {"anchorCell": {"sheetId": ws.id, "rowIndex": 30, "columnIndex": 6}, "widthPixels": 620, "heightPixels": 320}}
                        }
                    }
                }
            )

        sp.batch_update({"requests": chart_requests})
        print("[GSHEETS] Đã setup Dashboard đẹp hơn và thêm biểu đồ trực quan.")
    except Exception as e:
        print(f"[WARN] setup_dashboard lỗi: {e}")
