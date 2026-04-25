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

_SEMICOLON_LOCALE_PREFIXES = (
    "vi", "fr", "de", "es", "it", "pt", "ru", "tr", "pl", "nl",
    "cs", "sk", "hu", "uk", "ro", "bg", "hr", "sl", "sr", "da",
    "fi", "sv", "nb",
)

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


def _parse_trade_time(time_raw) -> datetime | None:
    txt = str(time_raw or "").strip()
    if not txt:
        return None
    try:
        return datetime.fromisoformat(txt)
    except Exception:
        try:
            return datetime.strptime(txt, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None


def _build_period_summary(trades: list[dict]) -> dict:
    buckets = {
        "day": defaultdict(lambda: {"trades": 0, "net_pnl": 0.0, "wins": 0, "losses": 0, "breakeven": 0}),
        "month": defaultdict(lambda: {"trades": 0, "net_pnl": 0.0, "wins": 0, "losses": 0, "breakeven": 0}),
        "year": defaultdict(lambda: {"trades": 0, "net_pnl": 0.0, "wins": 0, "losses": 0, "breakeven": 0}),
    }

    for t in trades:
        pnl = _to_float(t.get("pnl"), 0.0)
        dt = _parse_trade_time(t.get("time_close"))
        if dt is None:
            continue

        keys = {
            "day": dt.strftime("%Y-%m-%d"),
            "month": dt.strftime("%Y-%m"),
            "year": dt.strftime("%Y"),
        }
        for period, key in keys.items():
            item = buckets[period][key]
            item["trades"] += 1
            item["net_pnl"] += pnl
            if pnl > 0:
                item["wins"] += 1
            elif pnl < 0:
                item["losses"] += 1
            else:
                item["breakeven"] += 1

    result = {"day": [], "month": [], "year": []}
    keep = {"day": 31, "month": 18, "year": 10}
    for period in ("day", "month", "year"):
        for key in sorted(buckets[period].keys())[-keep[period]:]:
            row = buckets[period][key]
            total = int(row["trades"])
            wins = int(row["wins"])
            result[period].append({
                "period": key,
                "trades": total,
                "net_pnl": round(row["net_pnl"], 6),
                "wins": wins,
                "losses": int(row["losses"]),
                "breakeven": int(row["breakeven"]),
                "win_rate": round((wins / total * 100.0) if total else 0.0, 4),
            })
    return result


def get_eod_pnl_summary(target_date: str | None = None) -> dict:
    """
    Tổng hợp PnL theo ngày từ Trade_History (fallback Sheet1 nếu cần).
    target_date: chuỗi YYYY-MM-DD theo giờ VN.
    """
    sp = _get_spreadsheet()
    if sp is None:
        return {"ok": False, "reason": "Google Sheets chưa sẵn sàng."}

    rows = []
    try:
        rows = sp.worksheet("Trade_History").get_all_records()
    except Exception:
        try:
            rows = sp.sheet1.get_all_records()
        except Exception as e:
            return {"ok": False, "reason": f"Không đọc được dữ liệu lịch sử: {e}"}

    if not rows:
        return {"ok": False, "reason": "Chưa có giao dịch đã đóng để tổng hợp."}

    day_map: dict[str, dict] = defaultdict(lambda: {"trades": 0, "net_pnl": 0.0})
    for r in rows:
        dt = _parse_trade_time(r.get("Time_Close"))
        if dt is None:
            continue
        day_key = dt.strftime("%Y-%m-%d")
        pnl = _to_float(r.get("PnL"), 0.0)
        day_map[day_key]["trades"] += 1
        day_map[day_key]["net_pnl"] += pnl

    if not day_map:
        return {"ok": False, "reason": "Không parse được ngày giao dịch hợp lệ."}

    day_keys = sorted(day_map.keys())
    selected_date = target_date or now_vn().strftime("%Y-%m-%d")
    selected_day = day_map.get(selected_date, {"trades": 0, "net_pnl": 0.0})

    total_trades = sum(int(v["trades"]) for v in day_map.values())
    total_pnl = sum(float(v["net_pnl"]) for v in day_map.values())
    positive_days = sum(1 for v in day_map.values() if float(v["net_pnl"]) > 0)
    negative_days = sum(1 for v in day_map.values() if float(v["net_pnl"]) < 0)
    flat_days = sum(1 for v in day_map.values() if float(v["net_pnl"]) == 0)

    best_key = max(day_keys, key=lambda k: float(day_map[k]["net_pnl"]))
    worst_key = min(day_keys, key=lambda k: float(day_map[k]["net_pnl"]))

    return {
        "ok": True,
        "daily": {
            "date": selected_date,
            "trades": int(selected_day["trades"]),
            "net_pnl": float(selected_day["net_pnl"]),
        },
        "all_days": {
            "start_date": day_keys[0],
            "end_date": day_keys[-1],
            "total_days": len(day_keys),
            "total_trades": total_trades,
            "total_pnl": float(total_pnl),
            "positive_days": positive_days,
            "negative_days": negative_days,
            "flat_days": flat_days,
            "best_day": {"date": best_key, "net_pnl": float(day_map[best_key]["net_pnl"])},
            "worst_day": {"date": worst_key, "net_pnl": float(day_map[worst_key]["net_pnl"])},
        },
    }


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


def get_dashboard_payload(limit: int = 200) -> dict:
    """
    Trả dữ liệu để render web dashboard:
    - trades: lịch sử lệnh đóng gần nhất
    - pnl_curve: đường cong PnL lũy kế
    - metrics: thống kê tổng quan
    """
    payload = {
        "trades": [],
        "pnl_curve": [],
        "is_demo": False,
        "metrics": {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "wallet_balance": 0.0,
            "wallet_equity": 0.0,
            "wallet_available": 0.0,
            "wallet_used_margin": 0.0,
            "running_positions": 0,
            "running_pnl_total": 0.0,
            "running_pnl_positive": 0.0,
            "running_pnl_negative": 0.0,
        },
        "period_summary": {"day": [], "month": [], "year": []},
        "data_source": "Google Sheets + BingX",
    }

    sp = _get_spreadsheet()
    if sp is None:
        return payload

    try:
        trade_ws = sp.worksheet("Trade_History")
    except Exception:
        trade_ws = None

    try:
        pnl_ws = sp.worksheet("PnL_History")
    except Exception:
        pnl_ws = None

    try:
        active_ws = sp.worksheet("Active_Positions")
    except Exception:
        active_ws = None

    all_trade_rows = []

    try:
        if trade_ws is not None:
            rows = trade_ws.get_all_records()
            if rows:
                all_trade_rows = rows
                recent_rows = rows[-max(limit, 1):]
                for r in reversed(recent_rows):
                    pnl = _to_float(r.get("PnL"), 0.0)
                    if pnl > 0:
                        result = "WIN"
                    elif pnl < 0:
                        result = "LOSS"
                    else:
                        result = "BREAKEVEN"
                    payload["trades"].append({
                        "time_close": str(r.get("Time_Close", "") or ""),
                        "label": str(r.get("Mã Lệnh", "") or ""),
                        "symbol": str(r.get("Symbol", "") or ""),
                        "side": str(r.get("Side", "") or ""),
                        "strategy": str(r.get("Strategy", "") or ""),
                        "interval": str(r.get("Interval", "") or ""),
                        "pnl": round(pnl, 6),
                        "roi_pct": _to_float(r.get("ROI %"), 0.0),
                        "result": result,
                    })
    except Exception as e:
        print(f"[WARN] get_dashboard_payload/trade_rows lỗi: {e}")

    try:
        if active_ws is not None:
            running_rows = active_ws.get_all_records()
            running_pnls = [_to_float(r.get("PnL"), 0.0) for r in running_rows]
            payload["metrics"]["running_positions"] = len(running_pnls)
            payload["metrics"]["running_pnl_total"] = round(sum(running_pnls), 6)
            payload["metrics"]["running_pnl_positive"] = round(sum(v for v in running_pnls if v > 0), 6)
            payload["metrics"]["running_pnl_negative"] = round(sum(v for v in running_pnls if v < 0), 6)
    except Exception as e:
        print(f"[WARN] get_dashboard_payload/active_rows lỗi: {e}")

    try:
        if pnl_ws is not None:
            pnl_rows = pnl_ws.get_all_records()
            if pnl_rows:
                curve_rows = pnl_rows[-max(limit, 1):]
                for r in curve_rows:
                    payload["pnl_curve"].append({
                        "trade_no": int(_to_float(r.get("Trade #"), 0)),
                        "time_close": str(r.get("Time_Close", "") or ""),
                        "pnl": round(_to_float(r.get("PnL"), 0.0), 6),
                        "cumulative_pnl": round(_to_float(r.get("Cumulative PnL"), 0.0), 6),
                    })

                last = pnl_rows[-1]
                total_trades = int(_to_float(last.get("Trade #"), 0))
                wins = int(_to_float(last.get("Wins"), 0))
                losses = int(_to_float(last.get("Losses"), 0))
                be = int(_to_float(last.get("Breakeven"), 0))
                net_pnl = _to_float(last.get("Cumulative PnL"), 0.0)
                win_rate = (wins / total_trades * 100.0) if total_trades else 0.0

                payload["metrics"] = {
                    "total_trades": total_trades,
                    "wins": wins,
                    "losses": losses,
                    "breakeven": be,
                    "win_rate": round(win_rate, 4),
                    "net_pnl": round(net_pnl, 6),
                }
    except Exception as e:
        print(f"[WARN] get_dashboard_payload/pnl_rows lỗi: {e}")

    if payload["metrics"]["total_trades"] == 0 and payload["trades"]:
        wins = sum(1 for t in payload["trades"] if t["pnl"] > 0)
        losses = sum(1 for t in payload["trades"] if t["pnl"] < 0)
        be = sum(1 for t in payload["trades"] if t["pnl"] == 0)
        total = len(payload["trades"])
        net_pnl = sum(t["pnl"] for t in payload["trades"])
        payload["metrics"] = {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "breakeven": be,
            "win_rate": round((wins / total * 100.0) if total else 0.0, 4),
            "net_pnl": round(net_pnl, 6),
            "wallet_balance": payload["metrics"]["wallet_balance"],
            "wallet_equity": payload["metrics"]["wallet_equity"],
            "wallet_available": payload["metrics"]["wallet_available"],
            "wallet_used_margin": payload["metrics"]["wallet_used_margin"],
            "running_positions": payload["metrics"]["running_positions"],
            "running_pnl_total": payload["metrics"]["running_pnl_total"],
            "running_pnl_positive": payload["metrics"]["running_pnl_positive"],
            "running_pnl_negative": payload["metrics"]["running_pnl_negative"],
        }

    try:
        from bingx_client import bing_client, has_api_credentials
        if has_api_credentials():
            wallet = bing_client.get_balance_info("VST")
            payload["metrics"]["wallet_balance"] = round(_to_float(wallet.get("balance"), 0.0), 6)
            payload["metrics"]["wallet_equity"] = round(_to_float(wallet.get("equity"), 0.0), 6)
            payload["metrics"]["wallet_available"] = round(_to_float(wallet.get("availableMargin"), 0.0), 6)
            payload["metrics"]["wallet_used_margin"] = round(_to_float(wallet.get("usedMargin"), 0.0), 6)
    except Exception as e:
        print(f"[WARN] get_dashboard_payload/wallet lỗi: {e}")

    summary_source = [
        {
            "time_close": str(r.get("Time_Close", "") or ""),
            "pnl": _to_float(r.get("PnL"), 0.0),
        }
        for r in all_trade_rows
    ]
    payload["period_summary"] = _build_period_summary(summary_source)

    return payload


def get_demo_dashboard_payload() -> dict:
    """
    Dữ liệu mẫu để preview giao diện dashboard khi chưa có Google Sheets.
    """
    curve = []
    pnl_samples = [8.2, -3.1, 5.7, 4.6, -2.4, 6.3, -1.0, 9.4, -4.2, 7.8]
    cumulative = 0.0
    for idx, pnl in enumerate(pnl_samples, start=1):
        cumulative += pnl
        curve.append({
            "trade_no": idx,
            "time_close": f"2026-04-{idx:02d} 20:00:00",
            "pnl": pnl,
            "cumulative_pnl": round(cumulative, 4),
        })

    trades = [
        {
            "time_close": f"2026-04-{i:02d} 20:00:00",
            "label": f"LỆNH #{i}",
            "symbol": "BTC-USDT",
            "side": "LONG" if i % 2 else "SHORT",
            "strategy": "scalp",
            "interval": "15m",
            "pnl": curve[i - 1]["pnl"],
            "roi_pct": round(curve[i - 1]["pnl"] * 0.8, 2),
            "result": "WIN" if curve[i - 1]["pnl"] > 0 else "LOSS",
        }
        for i in range(10, 0, -1)
    ]

    wins = sum(1 for x in pnl_samples if x > 0)
    losses = sum(1 for x in pnl_samples if x < 0)
    total = len(pnl_samples)
    return {
        "trades": trades,
        "pnl_curve": curve,
        "is_demo": True,
        "metrics": {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "breakeven": total - wins - losses,
            "win_rate": round((wins / total) * 100.0, 2),
            "net_pnl": round(sum(pnl_samples), 4),
        },
    }


def get_readonly_site_payload(limit: int = 300) -> dict:
    """
    Payload tổng cho website read-only nhiều trang:
    - overview: KPI + curve + recent trades + period summary
    - trade_history: toàn bộ lịch sử đã đóng
    - running_positions: lệnh đang chạy
    - analytics: dữ liệu tổng hợp theo day/month/year
    - system: trạng thái nguồn dữ liệu
    """
    overview = get_dashboard_payload(limit=limit)
    payload = {
        "overview": overview,
        "trade_history": [],
        "running_positions": [],
        "analytics": {
            "day": overview.get("period_summary", {}).get("day", []),
            "month": overview.get("period_summary", {}).get("month", []),
            "year": overview.get("period_summary", {}).get("year", []),
        },
        "system": {
            "data_source": overview.get("data_source", "Google Sheets + BingX"),
            "sheet_connected": False,
            "last_trade_time": "",
            "last_running_update": "",
            "errors": [],
        },
    }

    sp = _get_spreadsheet()
    if sp is None:
        payload["system"]["errors"].append("Không kết nối được Google Sheets.")
        return payload

    payload["system"]["sheet_connected"] = True

    # Trade history đầy đủ
    try:
        trade_ws = sp.worksheet("Trade_History")
        trade_rows = trade_ws.get_all_records()
        for r in reversed(trade_rows):
            item = {
                "trade_no": int(_to_float(r.get("Trade #"), 0.0)),
                "time_close": str(r.get("Time_Close", "") or ""),
                "label": str(r.get("Mã Lệnh", "") or ""),
                "symbol": str(r.get("Symbol", "") or ""),
                "side": str(r.get("Side", "") or ""),
                "strategy": str(r.get("Strategy", "") or ""),
                "interval": str(r.get("Interval", "") or ""),
                "pnl": round(_to_float(r.get("PnL"), 0.0), 6),
                "roi_pct": _to_float(r.get("ROI %"), 0.0),
                "result": str(r.get("Result", "") or ""),
                "duration_mins": _to_float(r.get("Duration (Mins)"), 0.0),
            }
            payload["trade_history"].append(item)
        if payload["trade_history"]:
            payload["system"]["last_trade_time"] = payload["trade_history"][0]["time_close"]
    except Exception as e:
        payload["system"]["errors"].append(f"Trade_History lỗi: {e}")

    # Running positions
    try:
        active_ws = sp.worksheet("Active_Positions")
        active_rows = active_ws.get_all_records()
        for r in active_rows:
            payload["running_positions"].append({
                "symbol": str(r.get("Symbol", "") or ""),
                "label": str(r.get("Mã Lệnh", "") or ""),
                "side": str(r.get("Side", "") or ""),
                "mode": str(r.get("Mode", "") or ""),
                "entry": _to_float(r.get("Entry"), 0.0),
                "live_price": _to_float(r.get("Live Price"), 0.0),
                "sl": _to_float(r.get("SL"), 0.0),
                "tp": _to_float(r.get("TP"), 0.0),
                "pnl": _to_float(r.get("PnL"), 0.0),
                "roi_pct": _to_float(r.get("ROI %"), 0.0),
                "time_opened": str(r.get("Time Opened", "") or ""),
            })
        if payload["running_positions"]:
            payload["system"]["last_running_update"] = now_vn().strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        payload["system"]["errors"].append(f"Active_Positions lỗi: {e}")

    return payload

def setup_dashboard():
    """
    Tạo hoặc đảm bảo Sheet Dashboard tồn tại với bố cục đẹp hơn và có biểu đồ trực quan.
    """
    ws = _get_or_create_worksheet("Dashboard", rows="180", cols="10")
    if ws is None:
        return

    try:
        sp = _get_spreadsheet()
        if sp is None:
            return
        locale = ""
        try:
            locale = str(sp.fetch_sheet_metadata().get("properties", {}).get("locale", "")).lower()
        except Exception:
            locale = ""
        arg_sep = ";" if locale.startswith(_SEMICOLON_LOCALE_PREFIXES) else ","

        def _fx(formula: str) -> str:
            if arg_sep == ";":
                return formula
            return formula.replace(";", ",")

        history_ws = None
        history_ref = "'Sheet1'"
        try:
            history_ws = sp.sheet1
            if history_ws.title != "Sheet1":
                history_ws.update_title("Sheet1")
            history_ref = "'Sheet1'"
        except Exception:
            try:
                history_ws = sp.sheet1
                safe_title = history_ws.title.replace("'", "''")
                history_ref = f"'{safe_title}'"
            except Exception:
                history_ws = None

        data = [
            ["📈 SMC BOT DASHBOARD", "", "", "", "", "", "", "", "", ""],
            ["Last Sync", _fx(f'=IFERROR(TEXT(MAX({history_ref}!A2:A);"yyyy-mm-dd hh:mm:ss"); "N/A")'), "", "Timezone", "UTC", "", "", "", "", ""],
            ["", "", "", "", "", "", "", "", "", ""],
            ["📊 Tổng quan", "Giá trị", "", "⚡ Hiệu suất", "Giá trị", "", "🛡️ Rủi ro", "Giá trị", "", ""],
            ["Tổng lệnh", _fx(f'=COUNTA({history_ref}!B2:B)'), "", "Win rate", _fx(f'=IF(B5=0;0;COUNTIF({history_ref}!N2:N;"WIN")/B5)'), "", "Profit factor", _fx(f'=IF(ABS(SUMIF({history_ref}!K2:K;"<0"))=0;"inf";SUMIF({history_ref}!K2:K;">0")/ABS(SUMIF({history_ref}!K2:K;"<0")))'), "", ""],
            ["WIN", _fx(f'=COUNTIF({history_ref}!N2:N;"WIN")'), "", "Avg PnL", _fx(f'=IF(B5=0;0;SUM({history_ref}!K2:K)/B5)'), "", "Max DD", _fx(f'=IFERROR(MIN(ArrayFormula(SUMIF(ROW({history_ref}!K2:K);"<="&ROW({history_ref}!K2:K);{history_ref}!K2:K)-MAX(FILTER(ArrayFormula(SUMIF(ROW({history_ref}!K2:K);"<="&ROW({history_ref}!K2:K);{history_ref}!K2:K));ROW({history_ref}!K2:K)<=ROW({history_ref}!K2:K))));0)'), "", ""],
            ["LOSS", _fx(f'=COUNTIF({history_ref}!N2:N;"LOSS")'), "", "Avg WIN", _fx(f'=IF(B6=0;0;SUMIF({history_ref}!K2:K;">0")/B6)'), "", "Best trade", _fx(f'=IFERROR(MAX({history_ref}!K2:K);0)'), "", ""],
            ["BREAKEVEN", _fx(f'=COUNTIF({history_ref}!N2:N;"BREAKEVEN")'), "", "Avg LOSS", _fx(f'=IF(B7=0;0;SUMIF({history_ref}!K2:K;"<0")/B7)'), "", "Worst trade", _fx(f'=IFERROR(MIN({history_ref}!K2:K);0)'), "", ""],
            ["Net PnL", _fx(f'=SUM({history_ref}!K2:K)'), "", "Payoff ratio", _fx('=IF(ABS(E8)=0;0;E7/ABS(E8))'), "", "", "", "", ""],
            ["", "", "", "", "", "", "", "", "", ""],
            ["📅 Daily PnL (30 ngày)", "PnL", "", "📅 Weekly PnL (16 tuần)", "PnL", "", "", "", "", ""],
            [
                _fx(f'=IFERROR(TEXT(INDEX(SORT(UNIQUE(FILTER(IFERROR(DATEVALUE(LEFT({history_ref}!A2:A;10)););{history_ref}!A2:A<>""));1;FALSE);SEQUENCE(30;1;1;1));"yyyy-mm-dd");"")'),
                _fx(f'=IF(A12="";"";SUM(FILTER({history_ref}!K2:K;IFERROR(DATEVALUE(LEFT({history_ref}!A2:A;10));)=A12)))'),
                "",
                _fx(f'=IFERROR(INDEX(SORT(UNIQUE(FILTER(TEXT(IFERROR(DATEVALUE(LEFT({history_ref}!A2:A;10)););"yyyy")&"-W"&TEXT(ISOWEEKNUM(IFERROR(DATEVALUE(LEFT({history_ref}!A2:A;10));));"00");{history_ref}!A2:A<>""));1;FALSE);SEQUENCE(16;1;1;1));"")'),
                _fx(f'=IF(D12=""; ""; SUM(FILTER({history_ref}!K2:K; TEXT(IFERROR(DATEVALUE(LEFT({history_ref}!A2:A;10)););"yyyy")&"-W"&TEXT(ISOWEEKNUM(IFERROR(DATEVALUE(LEFT({history_ref}!A2:A;10));));"00")=D12)))'),
                "",
                "",
                "",
                "",
                "",
            ],
        ]

        ws.clear()
        ws.update(data, 'A1', value_input_option='USER_ENTERED')

        history_id = None
        try:
            if history_ws is not None:
                history_id = history_ws.id
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
