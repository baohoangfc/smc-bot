# Kế hoạch hoàn thiện website hiển thị cho SMC Bot (read-only)

## 1) Mục tiêu sản phẩm
Website chỉ để **quan sát** (không đặt lệnh, không chỉnh sửa dữ liệu), tập trung vào:
- Tình trạng bot theo thời gian thực.
- Lịch sử giao dịch chính xác từ nguồn chuẩn (Google Sheets và/hoặc BingX).
- Tổng quan hiệu suất: lãi/lỗ, winrate, drawdown, chuỗi thắng/thua.
- Theo dõi vốn: balance/equity/margin.
- Theo dõi lệnh chạy: số lệnh, PnL dương/âm/tổng.
- Báo cáo theo chu kỳ thời gian: ngày, tháng, năm.

## 2) Nguyên tắc dữ liệu (quan trọng nhất)

### 2.1. Nguồn dữ liệu chuẩn
- **Lịch sử lệnh đóng**: ưu tiên `Trade_History` + `PnL_History` trong Google Sheets (đã được bot ghi chuẩn).
- **Lệnh đang chạy**: ưu tiên `Active_Positions` trong Google Sheets.
- **Ví/Balance**: lấy trực tiếp BingX API (`balance/equity/availableMargin/usedMargin`) nếu có API key.

### 2.2. Chuẩn hóa schema dữ liệu nội bộ
Tạo 1 lớp chuẩn hóa để mọi màn hình dùng chung:
- `trade`: `time_close`, `symbol`, `side`, `strategy`, `interval`, `pnl`, `roi_pct`, `result`.
- `running_position`: `symbol`, `side`, `entry`, `live_price`, `pnl`, `roi_pct`, `opened_at`.
- `wallet`: `balance`, `equity`, `available_margin`, `used_margin`.

### 2.3. Data quality checks
- Check timestamp hợp lệ và timezone nhất quán.
- Check cột thiếu/null trong từng sheet.
- Check trùng `Trade #` hoặc thiếu chuỗi liên tục.
- Nếu lỗi nguồn, UI hiển thị trạng thái degraded + thời điểm cập nhật cuối.

## 3) Thiết kế thông tin (Information Architecture)

## Trang 1: Overview Dashboard (chính)
- Bot status: online/offline, lần sync gần nhất.
- KPI cards:
  - Tổng lệnh, Win/Loss/BE, Winrate, Net PnL.
  - Wallet: Balance/Equity/Available/Used margin.
  - Running: số lệnh chạy, PnL +, PnL -, PnL tổng.
- Biểu đồ:
  - Cumulative PnL.
  - Daily PnL 30 ngày.
  - Monthly PnL 12–18 tháng.
- Bảng giao dịch gần nhất (lọc read-only).

## Trang 2: Trade History
- Bảng đầy đủ lịch sử đóng lệnh.
- Cột quan trọng: thời gian, mã lệnh, symbol, side, setup, pnl, roi, duration.
- Filter hiển thị (client-side hoặc server query): theo symbol, side, strategy, khoảng thời gian.
- Export CSV (read-only download).

## Trang 3: Running Positions
- Danh sách lệnh đang chạy realtime.
- Tổng hợp nhanh theo symbol và side.
- Cảnh báo màu: ROI/PnL âm sâu.

## Trang 4: Performance Analytics
- Thống kê theo **ngày/tháng/năm**.
- Profit factor, payoff ratio, avg win/loss, expectancy.
- Max drawdown và chuỗi thắng/thua dài nhất.
- Top symbol thắng/thua.

## Trang 5: System & Data Health
- Nguồn dữ liệu đang dùng (Sheets/BingX).
- Độ trễ dữ liệu, số lần sync lỗi gần nhất.
- Nhật ký lỗi rút gọn để debug vận hành.

## 4) Kế hoạch triển khai theo phase

## Phase A — Củng cố backend dữ liệu (P0)
1. Refactor `get_dashboard_payload` thành nhiều service nhỏ:
   - `load_trade_history()`
   - `load_running_positions()`
   - `load_wallet_snapshot()`
   - `build_kpis()`
   - `build_period_aggregates()`
2. Thêm cache ngắn hạn (30–60s) cho API dashboard.
3. Thêm `data_freshness` và `source_status` vào payload.

**Kết quả:** API ổn định, payload có cấu trúc rõ ràng, dễ mở rộng.

## Phase B — Hoàn thiện UI Dashboard (P0)
1. Chia layout section rõ ràng: KPI / Charts / Tables.
2. Chuẩn màu dương-âm và format số tiền/phần trăm.
3. Tối ưu mobile responsive.
4. Thêm trạng thái empty/loading/error minh bạch.

**Kết quả:** người dùng nhìn 1 trang là nắm được bot đang hoạt động thế nào.

## Phase C — Các trang chuyên sâu (P1)
1. Trade History page.
2. Running Positions page.
3. Performance Analytics page.
4. System & Data Health page.

**Kết quả:** website “đủ thông tin bot cần” nhưng vẫn read-only.

## Phase D — Tin cậy vận hành (P1)
1. Logging chuẩn hóa cho dashboard API.
2. Health endpoint riêng cho data pipeline.
3. Test tự động:
   - unit test parse/aggregate,
   - integration test mock gsheets/bingx,
   - snapshot test UI.

**Kết quả:** giảm lỗi sai số liệu khi deploy/restart bot.

## 5) Danh sách chỉ số bắt buộc (Definition of Done)
- [ ] Hiển thị đúng tổng số lệnh, win/loss/be, winrate, net pnl.
- [ ] Hiển thị đúng số dư ví: balance/equity/available/used margin.
- [ ] Hiển thị đúng lệnh chạy: count, pnl dương, pnl âm, pnl tổng.
- [ ] Có tổng hợp theo ngày/tháng/năm.
- [ ] Có biểu đồ cumulative pnl + daily/monthly pnl.
- [ ] Có thời điểm sync gần nhất và cảnh báo khi dữ liệu stale.
- [ ] Khi thiếu API key BingX, website vẫn chạy bình thường (graceful fallback).
- [ ] Hoàn toàn read-only (không nút trade, không chỉnh sửa dữ liệu).

## 6) Đề xuất kỹ thuật triển khai nhanh
- Giữ Flask + Jinja hiện tại để ship nhanh.
- Với dữ liệu lớn: bổ sung endpoint JSON phân trang cho lịch sử giao dịch.
- Tách logic business ra module riêng (`dashboard_service.py`) để dễ test.
- Chuẩn hóa timezone về UTC trong data layer, chỉ format VN khi render.

## 7) Timeline đề xuất
- P0 (Phase A+B): 2–3 ngày.
- P1 (Phase C): 2–4 ngày.
- P1 hardening (Phase D): 1–2 ngày.

Tổng: 5–9 ngày để có website hoàn chỉnh, read-only, đủ thông tin vận hành bot.
