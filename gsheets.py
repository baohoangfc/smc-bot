import os
import json
from datetime import datetime

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
    except Exception as e:
        print(f"[WARN] export_trade_to_sheet lỗi: {e}")

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
        print("[GSHEETS] Setup Dashboard thành công.")
    except Exception as e:
        print(f"[WARN] setup_dashboard lỗi: {e}")
