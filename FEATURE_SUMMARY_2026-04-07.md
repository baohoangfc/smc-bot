# Tổng hợp tính năng hiện có của SMC Bot (07/04/2026)

## 1) Vận hành & kiến trúc
- Chạy vòng lặp chính theo mô hình bot giao dịch, đồng thời có Flask health-check endpoint (`/`) để nền tảng deploy kiểm tra trạng thái sống.
- Dùng background fetcher theo từng symbol để lấy dữ liệu đa khung thời gian song song, giảm nghẽn khi chạy nhiều cặp.
- Hỗ trợ chạy nhiều symbol từ biến môi trường (`BINGX_SYMBOLS`).

## 2) Signal engine
- Hỗ trợ nhiều engine tín hiệu:
  - `strict`: SMC scalp theo bias + sweep + MSS + quality scoring.
  - `backtest_v5`: engine backtest dùng cho scan tín hiệu.
  - `swing`: tái dùng logic backtest_v5, nâng RR mục tiêu cho swing.
  - `grid_fast`: scalp theo độ lệch giá so với anchor (mean reversion theo nấc grid).
- Có router chọn engine tự động theo chế độ run (`resolve_signal_engine`): read-only ưu tiên backtest_v5, auto-trade ưu tiên strict.
- Có cơ chế chọn tín hiệu tốt nhất từ nhiều candidate theo điểm ưu tiên (quality, RR, chiến lược, điều kiện thanh khoản).

## 3) Bộ lọc trước vào lệnh
- Gate chất lượng tín hiệu (`MIN_SIGNAL_QUALITY_SCORE`).
- Gate thanh khoản theo khung giờ VN (soft/strict mode).
- Gate drift entry: bỏ tín hiệu nếu giá thị trường lệch quá xa so với entry tính toán.
- Gate cooldown theo bucket signal (side + strategy + interval), có giảm cooldown cho tín hiệu quality cao.
- Bootstrap chống vào lệnh ngay tín hiệu đầu tiên sau restart.

## 4) Thực thi lệnh & quản trị danh mục
- Hỗ trợ 2 chế độ:
  - READ-ONLY: chỉ cảnh báo tín hiệu, không đặt/đóng lệnh.
  - AUTO-TRADE: đặt market order + gắn TP/SL.
- Quản lý giới hạn số lệnh mở động theo thanh khoản (giờ cao/giờ thấp).
- Khi có tín hiệu mới ngược chiều hoặc vượt limit, bot có thể đóng bớt vị thế kém hiệu quả trước khi mở lệnh mới.
- Tính khối lượng lệnh theo margin cấu hình và leverage; tăng margin cho tín hiệu chất lượng cao.

## 5) Quản trị rủi ro khi giữ lệnh
- Breakeven: tự dời SL về BE khi tiến độ đạt ngưỡng.
- Trailing Stop: cập nhật SL bám giá, chỉ kích hoạt sau BE.
- Partial TP: chốt một phần khối lượng (mặc định 50%) khi ROI đạt ngưỡng (mặc định 100%).
- Đồng bộ TP/SL giữa state local và sàn để tránh thiếu lệnh bảo vệ.
- Theo dõi PnL theo ROI ký quỹ và gửi cảnh báo khi biến động theo step.

## 6) Persistence & self-healing
- Lưu/khôi phục `learning_state` và `active_positions` qua Firestore (nếu có cấu hình), fallback file local.
- Khi restart, bot khôi phục trạng thái lệnh đang theo dõi, đồng thời check vị thế thực trên sàn để chống lệch trạng thái.
- Cơ chế lưu learning theo batch (dirty flag + flush interval), tránh ghi quá dày.

## 7) Learning layer
- Ghi nhận hiệu quả theo key `symbol|strategy|interval|side`: trades, wins/losses, win_rate, avg_pnl.
- Tự điều chỉnh quality score và RR mục tiêu của tín hiệu dựa trên hiệu suất lịch sử (khi đủ số mẫu tối thiểu).

## 8) Notification & báo cáo
- Telegram đầy đủ luồng: startup, signal, order result, status chờ, theo dõi PnL, tổng kết khi đóng hết vị thế.
- Có chống gửi trùng message trong cửa sổ thời gian ngắn.
- Tích hợp Google Sheets:
  - Ghi log lệnh đóng.
  - Đồng bộ active positions định kỳ.
  - Tạo sheet tổng hợp lợi nhuận (win rate, PF, MDD, daily/weekly).
- Có script `profit_report.py` để xuất báo cáo lợi nhuận từ CSV hoặc trực tiếp từ Google Sheet.

## 9) Chỉ báo & dữ liệu kỹ thuật
- Tự tính EMA50/EMA200, RSI, ATR/ATR%.
- Hỗ trợ phát hiện swing highs/lows phục vụ logic cấu trúc và sweep.

## 10) Tình trạng “SMC chuẩn”
- Bot hiện ở mức SMC-lite/hybrid: đã có nhiều thành phần SMC thực dụng (sweep, MSS, premium/discount, bias),
  nhưng chưa đầy đủ workflow ICT/SMC chuẩn toàn phần (HTF/LTF structure tách lớp sâu, POI bắt buộc OB/FVG, invalidation thuần cấu trúc).
