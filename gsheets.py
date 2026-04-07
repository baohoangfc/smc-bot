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
        win_rate = (wins / total_trades * 100.0) if total_trades else 0.0
        profit_factor = _safe_profit_factor(gross_profit, gross_loss)

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
                out.append((k, n, pnl_sum, pnl_sum / n if n else 0.0))
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
            ["Profit factor", pf_text],
            ["Max drawdown", round(max_drawdown, 6)],
            [],
            ["Daily summary", "", "", ""],
            ["date", "trades", "net_pnl", "avg_pnl"],
        ]
        for d, n, pnl_sum, avg in daily_rows[-30:]:
            summary_rows.append([d, n, round(pnl_sum, 6), round(avg, 6)])

        summary_rows += [[], ["Weekly summary", "", "", ""], ["week", "trades", "net_pnl", "avg_pnl"]]
        for w, n, pnl_sum, avg in weekly_rows[-16:]:
            summary_rows.append([w, n, round(pnl_sum, 6), round(avg, 6)])

        summary_ws.clear()
        summary_ws.update(summary_rows, "A1")
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
    Tạo hoặc đẩm bảo Sheet Dashboard tồn tại với các công thức tính toán.
    """
    ws = _get_or_create_worksheet("Dashboard")
    if ws is None:
        return
        
    try:
        if len(ws.col_values(1)) > 0:
            return # Đã setup
            
        data = [
            ["BẢNG THỐNG KÊ GIAO DỊCH (DASHBOARD)", ""],
            ["", ""],
            ["Tổng số lệnh:", '=COUNTA(Sheet1!B2:B)'],
            ["Số lệnh WIN:", '=COUNTIF(Sheet1!N2:N, "WIN")'],
            ["Số lệnh LOSS:", '=COUNTIF(Sheet1!N2:N, "LOSS")'],
            ["Winrate (%):", '=IF(B3=0, 0, B4/B3)'], # Format cột B6 thành % trong sheet bằng tay
            ["", ""],
            ["Tổng Lợi Nhuận (PnL):", '=SUM(Sheet1!K2:K)'],
            ["Ghi chú:", "Để lấy dữ liệu update, xem sheet Lịch Sử (Sheet1)"]
        ]
        
        ws.update(data, 'A1')
        print("[GSHEETS] Đã điền công thức Dashboard.")
        
        # Tạo biểu đồ
        try:
            sp = _get_spreadsheet()
            dashboard_id = ws.id
            history_id = None
            try:
                history_id = sp.sheet1.id
            except:
                pass
                
            requests = []
            
            # Biểu đồ tròn: Win/Loss Ratio
            requests.append({
                "addChart": {
                    "chart": {
                        "spec": {
                            "title": "Tỉ Lệ Thắng/Thua",
                            "pieChart": {
                                "legendPosition": "RIGHT_LEGEND",
                                "domain": {"sourceRange": {"sources": [{"sheetId": dashboard_id, "startRowIndex": 3, "endRowIndex": 5, "startColumnIndex": 0, "endColumnIndex": 1}]}},
                                "series": {"sourceRange": {"sources": [{"sheetId": dashboard_id, "startRowIndex": 3, "endRowIndex": 5, "startColumnIndex": 1, "endColumnIndex": 2}]}}
                            }
                        },
                        "position": {
                            "overlayPosition": {
                                "anchorCell": {"sheetId": dashboard_id, "rowIndex": 1, "columnIndex": 3},
                                "widthPixels": 400, "heightPixels": 280
                            }
                        }
                    }
                }
            })
            
            # Biểu đồ cột: PnL theo lệnh
            if history_id is not None:
                requests.append({
                    "addChart": {
                        "chart": {
                            "spec": {
                                "title": "Hiệu Suất Lợi Nhuận (PnL History)",
                                "basicChart": {
                                    "chartType": "COLUMN",
                                    "legendPosition": "BOTTOM_LEGEND",
                                    "axis": [
                                        {"position": "BOTTOM_AXIS", "title": "Các Khớp Lệnh"},
                                        {"position": "LEFT_AXIS", "title": "USDT"}
                                    ],
                                    "series": [{
                                        "series": {"sourceRange": {"sources": [{"sheetId": history_id, "startRowIndex": 1, "startColumnIndex": 10, "endColumnIndex": 11}]}}
                                    }]
                                }
                            },
                            "position": {
                                "overlayPosition": {
                                    "anchorCell": {"sheetId": dashboard_id, "rowIndex": 11, "columnIndex": 0},
                                    "widthPixels": 600, "heightPixels": 350
                                }
                            }
                        }
                    }
                })
                
            sp.batch_update({"requests": requests})
            print("[GSHEETS] Tạo biểu đồ Dashboard thành công.")
        except Exception as e:
            print(f"[WARN] Lỗi vẽ biểu đồ Dashboard: {e}")
            
    except Exception as e:
        print(f"[WARN] setup_dashboard lỗi: {e}")
