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
_worksheet = None

def _get_gsheet_client():
    global _client, _worksheet
    if not _GSPREAD_AVAILABLE or not GOOGLE_SHEETS_CREDENTIALS_JSON or not GOOGLE_SHEET_ID:
        return None
        
    if _worksheet is not None:
        return _worksheet
        
    try:
        cred_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS_JSON)
        # Setup scope
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(cred_dict, scope) # type: ignore
        _client = gspread.authorize(creds)
        spreadsheet = _client.open_by_key(GOOGLE_SHEET_ID)
        
        # Thử lấy worksheet đầu tiên
        _worksheet = spreadsheet.sheet1
        
        # Nếu dòng 1 trống, tạo Header
        if len(_worksheet.col_values(1)) == 0:
            headers = [
                "Time_Close", "Mã Lệnh", "Symbol", "Side", "Strategy", "Interval", 
                "Entry", "Close Price", "SL", "TP", "PnL", "ROI %", "Duration (Mins)"
            ]
            _worksheet.insert_row(headers, 1)
            
        print("[GSHEETS] Khởi tạo kết nối Google Sheet thành công.")
        return _worksheet
    except Exception as e:
        print(f"[GSHEETS] Khởi tạo thất bại: {e}")
        return None

def export_trade_to_sheet(position: dict, pnl: float, close_price: float, symbol: str):
    ws = _get_gsheet_client()
    if ws is None:
        return

    try:
        from position_mgmt import calc_position_notional_base
        
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
        
        entry = float(position.get("entry", 0))
        sl = float(position.get("sl", 0))
        tp = float(position.get("tp", 0))
        
        # ROI %
        notional_base = calc_position_notional_base(position)
        leverage = float(position.get("leverage", 100))
        margin_base = max(notional_base / leverage, 0.0001)
        roi_pct = round((pnl / margin_base) * 100, 2)
        
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
            duration_mins
        ]
        
        ws.append_row(row_data)
        print(f"[GSHEETS] Đã đồng bộ lệnh {label} ({symbol}) lên Google Sheet.")
    except Exception as e:
        print(f"[WARN] export_trade_to_sheet lỗi: {e}")
