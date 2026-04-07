"""
profit_report.py — Báo cáo lợi nhuận từ lịch sử lệnh đóng.

Hỗ trợ input CSV (ví dụ export từ Google Sheet Sheet1) với cột mặc định:
- Time_Close
- PnL
- Result (optional)

Ví dụ:
  python profit_report.py --csv trades.csv
  python profit_report.py --csv trades.csv --start 2026-03-01 --end 2026-04-07
"""
from __future__ import annotations

import argparse
import math
import json
import re
from dataclasses import dataclass

import pandas as pd

from config import GOOGLE_SHEETS_CREDENTIALS_JSON, GOOGLE_SHEET_ID

try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    _GSHEET_AVAILABLE = True
except ImportError:
    _GSHEET_AVAILABLE = False


@dataclass
class Metrics:
    total_trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate_pct: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    avg_pnl: float
    expectancy: float
    profit_factor: float
    max_drawdown: float


def _safe_profit_factor(gross_profit: float, gross_loss: float) -> float:
    if gross_loss == 0:
        return math.inf if gross_profit > 0 else 0.0
    return gross_profit / abs(gross_loss)


def compute_metrics(df: pd.DataFrame, pnl_col: str) -> Metrics:
    pnl = pd.to_numeric(df[pnl_col], errors="coerce").fillna(0.0)
    total = int(len(df))
    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    breakeven = int((pnl == 0).sum())

    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(pnl[pnl < 0].sum())
    net_pnl = float(pnl.sum())
    avg_pnl = float(net_pnl / total) if total else 0.0
    expectancy = avg_pnl
    win_rate_pct = float((wins / total) * 100.0) if total else 0.0
    profit_factor = float(_safe_profit_factor(gross_profit, gross_loss))

    equity_curve = pnl.cumsum()
    running_peak = equity_curve.cummax()
    drawdown = equity_curve - running_peak
    max_drawdown = float(drawdown.min()) if len(drawdown) else 0.0

    return Metrics(
        total_trades=total,
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        win_rate_pct=win_rate_pct,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net_pnl,
        avg_pnl=avg_pnl,
        expectancy=expectancy,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tạo báo cáo lợi nhuận từ file CSV lịch sử lệnh.")
    parser.add_argument("--csv", default=None, help="Đường dẫn file CSV input.")
    parser.add_argument("--from-sheet", action="store_true", help="Đọc trực tiếp từ Google Sheet thay vì CSV.")
    parser.add_argument("--sheet-id", default=GOOGLE_SHEET_ID or None, help="Google Sheet ID.")
    parser.add_argument("--sheet-url", default=None, help="Google Sheet URL (có thể truyền thay cho --sheet-id).")
    parser.add_argument("--worksheet", default="Sheet1", help="Tên worksheet chứa lịch sử lệnh.")
    parser.add_argument(
        "--write-summary-sheet",
        default=None,
        help="Tên worksheet để ghi kết quả report (chỉ dùng khi có quyền Google Sheet).",
    )
    parser.add_argument("--time-col", default="Time_Close", help="Tên cột thời gian đóng lệnh.")
    parser.add_argument("--pnl-col", default="PnL", help="Tên cột PnL.")
    parser.add_argument("--start", default=None, help="Ngày bắt đầu lọc (YYYY-MM-DD).")
    parser.add_argument("--end", default=None, help="Ngày kết thúc lọc (YYYY-MM-DD).")
    parser.add_argument("--top", type=int, default=10, help="Số dòng daily/weekly summary in ra.")
    return parser.parse_args()


def _extract_sheet_id(sheet_id: str | None, sheet_url: str | None) -> str | None:
    if sheet_id:
        return sheet_id.strip()
    if not sheet_url:
        return None
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_url)
    return match.group(1) if match else None


def load_from_google_sheet(sheet_id: str, worksheet_name: str) -> pd.DataFrame:
    if not _GSHEET_AVAILABLE:
        raise RuntimeError("Thiếu gspread/oauth2client. Hãy cài dependencies trong requirements.txt")
    if not GOOGLE_SHEETS_CREDENTIALS_JSON:
        raise RuntimeError("Thiếu GOOGLE_SHEETS_CREDENTIALS_JSON trong environment.")

    cred_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS_JSON)
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(cred_dict, scope)  # type: ignore[arg-type]
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    ws = spreadsheet.worksheet(worksheet_name)
    rows = ws.get_all_records()
    return pd.DataFrame(rows)


def write_report_to_google_sheet(
    sheet_id: str,
    worksheet_name: str,
    metrics: Metrics,
    time_min,
    time_max,
    daily_df: pd.DataFrame,
    weekly_df: pd.DataFrame,
):
    if not _GSHEET_AVAILABLE:
        raise RuntimeError("Thiếu gspread/oauth2client. Hãy cài dependencies trong requirements.txt")
    if not GOOGLE_SHEETS_CREDENTIALS_JSON:
        raise RuntimeError("Thiếu GOOGLE_SHEETS_CREDENTIALS_JSON trong environment.")

    cred_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS_JSON)
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(cred_dict, scope)  # type: ignore[arg-type]
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    try:
        ws = spreadsheet.worksheet(worksheet_name)
    except gspread.exceptions.WorksheetNotFound:  # type: ignore[attr-defined]
        ws = spreadsheet.add_worksheet(title=worksheet_name, rows="1500", cols="20")

    pf = "inf" if math.isinf(metrics.profit_factor) else f"{metrics.profit_factor:.4f}"
    rows = [
        ["SMC BOT PROFIT REPORT", ""],
        ["Range", f"{time_min} -> {time_max}"],
        ["Total trades", metrics.total_trades],
        ["Wins/Loss/BE", f"{metrics.wins}/{metrics.losses}/{metrics.breakeven}"],
        ["Win rate (%)", round(metrics.win_rate_pct, 4)],
        ["Gross profit", round(metrics.gross_profit, 6)],
        ["Gross loss", round(metrics.gross_loss, 6)],
        ["Net PnL", round(metrics.net_pnl, 6)],
        ["Avg PnL/trade", round(metrics.avg_pnl, 6)],
        ["Expectancy", round(metrics.expectancy, 6)],
        ["Profit factor", pf],
        ["Max drawdown", round(metrics.max_drawdown, 6)],
        [],
        ["Daily summary", "", "", ""],
        ["date", "trades", "net_pnl", "avg_pnl"],
    ]
    for _, r in daily_df.iterrows():
        rows.append([str(r["date"]), int(r["trades"]), float(r["net_pnl"]), float(r["avg_pnl"])])

    rows += [[], ["Weekly summary", "", "", ""], ["week", "trades", "net_pnl", "avg_pnl"]]
    for _, r in weekly_df.iterrows():
        rows.append([str(r["week"]), int(r["trades"]), float(r["net_pnl"]), float(r["avg_pnl"])])

    rows += [
        [],
        ["Outcome", "Count"],
        ["WIN", metrics.wins],
        ["LOSS", metrics.losses],
        ["BREAKEVEN", metrics.breakeven],
    ]

    ws.clear()
    ws.update(rows, "A1")

    # Tô màu/định dạng cho dashboard nhìn dễ đọc hơn.
    daily_header_row = 15  # 1-indexed
    daily_data_start = 16
    daily_data_end_exclusive = daily_data_start + len(daily_df)
    weekly_header_row = daily_data_end_exclusive + 2
    weekly_data_start = weekly_header_row + 1
    weekly_data_end_exclusive = weekly_data_start + len(weekly_df)
    outcome_header_row = weekly_data_end_exclusive + 2
    outcome_data_start = outcome_header_row + 1
    outcome_data_end_exclusive = outcome_data_start + 3

    requests = [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 4,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.11, "green": 0.27, "blue": 0.55},
                        "horizontalAlignment": "CENTER",
                        "textFormat": {
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                            "fontSize": 13,
                            "bold": True,
                        },
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "endRowIndex": 12,
                    "startColumnIndex": 0,
                    "endColumnIndex": 2,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.95, "green": 0.97, "blue": 1.0},
                        "textFormat": {"bold": False, "fontSize": 10},
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": daily_header_row - 1,
                    "endRowIndex": daily_header_row,
                    "startColumnIndex": 0,
                    "endColumnIndex": 4,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.22, "green": 0.62, "blue": 0.33},
                        "textFormat": {
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                            "bold": True,
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
                    "sheetId": ws.id,
                    "startRowIndex": weekly_header_row - 1,
                    "endRowIndex": weekly_header_row,
                    "startColumnIndex": 0,
                    "endColumnIndex": 4,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.78, "green": 0.34, "blue": 0.14},
                        "textFormat": {
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                            "bold": True,
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
                    "sheetId": ws.id,
                    "startRowIndex": outcome_header_row - 1,
                    "endRowIndex": outcome_header_row,
                    "startColumnIndex": 0,
                    "endColumnIndex": 2,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.40, "green": 0.31, "blue": 0.70},
                        "textFormat": {
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                            "bold": True,
                        },
                        "horizontalAlignment": "CENTER",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 1,
                },
                "properties": {"pixelSize": 220},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": 1,
                    "endIndex": 2,
                },
                "properties": {"pixelSize": 260},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": 2,
                    "endIndex": 4,
                },
                "properties": {"pixelSize": 140},
                "fields": "pixelSize",
            }
        },
    ]

    # Format số cho cột PnL/avg ở bảng Daily + Weekly.
    if len(daily_df) > 0:
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": ws.id,
                        "startRowIndex": daily_data_start - 1,
                        "endRowIndex": daily_data_end_exclusive,
                        "startColumnIndex": 2,
                        "endColumnIndex": 4,
                    },
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.0000"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            }
        )

    if len(weekly_df) > 0:
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": ws.id,
                        "startRowIndex": weekly_data_start - 1,
                        "endRowIndex": weekly_data_end_exclusive,
                        "startColumnIndex": 2,
                        "endColumnIndex": 4,
                    },
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.0000"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            }
        )

    # Xóa chart cũ trong worksheet này trước khi tạo chart mới (tránh bị nhân bản chart mỗi lần chạy).
    try:
        metadata = spreadsheet.fetch_sheet_metadata()
        chart_ids = []
        for sheet_info in metadata.get("sheets", []):
            props = sheet_info.get("properties", {})
            if int(props.get("sheetId", -1)) != int(ws.id):
                continue
            for chart in sheet_info.get("charts", []):
                chart_id = chart.get("chartId")
                if chart_id is not None:
                    chart_ids.append(chart_id)
        for cid in chart_ids:
            requests.append({"deleteEmbeddedObject": {"objectId": cid}})
    except Exception:
        pass

    # Pie chart cho WIN/LOSS/BREAKEVEN
    requests.append(
        {
            "addChart": {
                "chart": {
                    "spec": {
                        "title": "Tỷ lệ Win/Loss/Breakeven",
                        "pieChart": {
                            "legendPosition": "RIGHT_LEGEND",
                            "domain": {
                                "sourceRange": {
                                    "sources": [
                                        {
                                            "sheetId": ws.id,
                                            "startRowIndex": outcome_data_start - 1,
                                            "endRowIndex": outcome_data_end_exclusive,
                                            "startColumnIndex": 0,
                                            "endColumnIndex": 1,
                                        }
                                    ]
                                }
                            },
                            "series": {
                                "sourceRange": {
                                    "sources": [
                                        {
                                            "sheetId": ws.id,
                                            "startRowIndex": outcome_data_start - 1,
                                            "endRowIndex": outcome_data_end_exclusive,
                                            "startColumnIndex": 1,
                                            "endColumnIndex": 2,
                                        }
                                    ]
                                }
                            },
                        },
                    },
                    "position": {
                        "overlayPosition": {
                            "anchorCell": {"sheetId": ws.id, "rowIndex": 1, "columnIndex": 4},
                            "widthPixels": 520,
                            "heightPixels": 300,
                        }
                    },
                }
            }
        }
    )

    # Line chart lợi nhuận theo ngày
    if len(daily_df) > 0:
        requests.append(
            {
                "addChart": {
                    "chart": {
                        "spec": {
                            "title": "Lợi nhuận ròng theo ngày",
                            "basicChart": {
                                "chartType": "LINE",
                                "legendPosition": "BOTTOM_LEGEND",
                                "axis": [
                                    {"position": "BOTTOM_AXIS", "title": "Ngày"},
                                    {"position": "LEFT_AXIS", "title": "Net PnL"},
                                ],
                                "domains": [
                                    {
                                        "domain": {
                                            "sourceRange": {
                                                "sources": [
                                                    {
                                                        "sheetId": ws.id,
                                                        "startRowIndex": daily_data_start - 1,
                                                        "endRowIndex": daily_data_end_exclusive,
                                                        "startColumnIndex": 0,
                                                        "endColumnIndex": 1,
                                                    }
                                                ]
                                            }
                                        }
                                    }
                                ],
                                "series": [
                                    {
                                        "series": {
                                            "sourceRange": {
                                                "sources": [
                                                    {
                                                        "sheetId": ws.id,
                                                        "startRowIndex": daily_data_start - 1,
                                                        "endRowIndex": daily_data_end_exclusive,
                                                        "startColumnIndex": 2,
                                                        "endColumnIndex": 3,
                                                    }
                                                ]
                                            }
                                        },
                                        "targetAxis": "LEFT_AXIS",
                                    }
                                ],
                            },
                        },
                        "position": {
                            "overlayPosition": {
                                "anchorCell": {"sheetId": ws.id, "rowIndex": 19, "columnIndex": 4},
                                "widthPixels": 640,
                                "heightPixels": 340,
                            }
                        },
                    }
                }
            }
        )

    spreadsheet.batch_update({"requests": requests})


def main() -> int:
    args = parse_args()
    sheet_id = _extract_sheet_id(args.sheet_id, args.sheet_url)

    if args.from_sheet:
        if not sheet_id:
            raise ValueError("Thiếu sheet id. Truyền --sheet-id hoặc --sheet-url.")
        df = load_from_google_sheet(sheet_id, args.worksheet)
    else:
        if not args.csv:
            raise ValueError("Khi không dùng --from-sheet, bắt buộc truyền --csv <file>.")
        df = pd.read_csv(args.csv)

    required = [args.time_col, args.pnl_col]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Thiếu cột bắt buộc: {missing}. Cột hiện có: {list(df.columns)}")

    df[args.time_col] = pd.to_datetime(df[args.time_col], errors="coerce", utc=False)
    df = df.dropna(subset=[args.time_col]).copy()

    if args.start:
        start_ts = pd.to_datetime(args.start)
        df = df[df[args.time_col] >= start_ts]
    if args.end:
        end_ts = pd.to_datetime(args.end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        df = df[df[args.time_col] <= end_ts]

    if df.empty:
        print("Không có dữ liệu sau khi lọc.")
        return 0

    df = df.sort_values(args.time_col).reset_index(drop=True)
    metrics = compute_metrics(df, args.pnl_col)

    print("=" * 72)
    print("SMC BOT - PROFIT REPORT")
    print("=" * 72)
    print(f"Range: {df[args.time_col].min()} -> {df[args.time_col].max()}")
    print(f"Total trades : {metrics.total_trades}")
    print(f"Wins/Loss/BE: {metrics.wins}/{metrics.losses}/{metrics.breakeven}")
    print(f"Win rate     : {metrics.win_rate_pct:.2f}%")
    print(f"Gross profit : {metrics.gross_profit:.4f}")
    print(f"Gross loss   : {metrics.gross_loss:.4f}")
    print(f"Net PnL      : {metrics.net_pnl:.4f}")
    print(f"Avg PnL/trade: {metrics.avg_pnl:.4f}")
    print(f"Expectancy   : {metrics.expectancy:.4f}")
    pf = "inf" if math.isinf(metrics.profit_factor) else f"{metrics.profit_factor:.4f}"
    print(f"Profit factor: {pf}")
    print(f"Max drawdown : {metrics.max_drawdown:.4f}")

    summary = df.copy()
    summary[args.pnl_col] = pd.to_numeric(summary[args.pnl_col], errors="coerce").fillna(0.0)
    summary["date"] = summary[args.time_col].dt.date
    summary["week"] = summary[args.time_col].dt.to_period("W-MON").astype(str)

    daily = summary.groupby("date", as_index=False)[args.pnl_col].agg(["count", "sum", "mean"])
    daily.columns = ["date", "trades", "net_pnl", "avg_pnl"]

    weekly = summary.groupby("week", as_index=False)[args.pnl_col].agg(["count", "sum", "mean"])
    weekly.columns = ["week", "trades", "net_pnl", "avg_pnl"]

    top_n = max(1, int(args.top))
    print("\n--- Daily summary ---")
    print(daily.tail(top_n).to_string(index=False))

    print("\n--- Weekly summary ---")
    print(weekly.tail(top_n).to_string(index=False))

    if args.write_summary_sheet:
        if not sheet_id:
            raise ValueError("Muốn ghi report lên Sheet thì cần --sheet-id hoặc --sheet-url.")
        write_report_to_google_sheet(
            sheet_id=sheet_id,
            worksheet_name=args.write_summary_sheet,
            metrics=metrics,
            time_min=df[args.time_col].min(),
            time_max=df[args.time_col].max(),
            daily_df=daily,
            weekly_df=weekly,
        )
        print(f"\nĐã ghi report vào worksheet: {args.write_summary_sheet}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
