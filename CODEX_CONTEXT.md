# SMC Bot – Codex Context (Single Source of Truth)

> Mục tiêu: Tổng hợp trạng thái code mới nhất để Codex có thể đọc nhanh và sửa đúng ngữ cảnh khi có thay đổi.

## 1) Version snapshot
- Context version: `v2026.06.05-1`
- Repo chính hiện tại: `smc-bot`
- File runtime cốt lõi: `bot.py`
- Các file hỗ trợ: `requirements.txt`, `Dockerfile`, `railway.toml`, `SMC_AUDIT.md`

## 1b) Thay đổi Phase 1 (04/04/2026)
- **Fix**: `scan_signal_backtest_v5` giờ tính và trả về `quality_score` (dựa OB body ratio, RSI zone, ATR%, EMA200 alignment). Trước đây thiếu field này khiến signal bị quality gate loại toàn bộ.
- **Fix**: Docstring của `scan_signal` đặt đúng vị trí đầu hàm (trước đây nằm sau `return` là dead code).
- **New**: Firebase Firestore persistent state layer (optional, backward-compatible):
  - `FIREBASE_CREDENTIALS_JSON` env var → Firestore client lazy-init
  - `load_learning_state` / `save_learning_state` → ưu tiên Firestore, fallback file local
  - `load_active_positions` / `save_active_positions` → persist `active_positions_by_symbol` qua restart
  - `save_active_positions` được gọi tại mọi điểm mutation (thêm lệnh, đóng lệnh, reset sau đóng hết)
- **Startup**: Khôi phục positions từ Firestore/file trước, sau đó check vị thế thực trên sàn, tránh đánh số label trùng.
- **Deps**: `firebase-admin==6.5.0` thêm vào `requirements.txt`

## 2) Luồng bot hiện tại (rút gọn)
1. Lấy dữ liệu giá/khung thời gian.
2. Sinh tín hiệu SMC theo các TF theo dõi.
3. Nếu có tín hiệu đạt điều kiện => gửi noti tín hiệu + vào lệnh (nếu bật trade).
4. Nếu chưa có tín hiệu và không có lệnh mở => gửi noti trạng thái theo chu kỳ.
5. Khi có vị thế mở => đồng bộ vị thế, theo dõi PnL, quản trị TP/SL, đóng vòng lệnh.

## 3) Notification contract hiện tại

### 3.1 Noti trạng thái chờ tín hiệu
- Hàm: `format_status_msg(symbol, last_price, candle_time, wait_reason=None)`
- Nội dung chính phải có:
  - Header: `SMC Bot - Cập nhật HH:MM (GMT+7)`
  - Giá hiện tại
  - `Khung TG` (INTERVAL)
  - `TF theo dõi` (SIGNAL_INTERVALS, fallback INTERVAL)
  - Nguồn dữ liệu
  - Số dư VST
  - Trạng thái chạy
  - Lý do chờ
  - Thời gian cập nhật tiếp theo
- Quy tắc độ dài:
  - `wait_reason` bị cắt nếu > 420 ký tự để tránh noti quá dài.

### 3.2 Noti lý do chờ (main loop)
- `wait_reason` hiện bao gồm:
  - Chưa có tín hiệu SMC mới ở TF theo dõi
  - Quality tối thiểu hiện tại (`MIN_SIGNAL_QUALITY_SCORE`)
  - Giới hạn lệnh mở (`current_max_active_orders`)
  - Ghi chú thanh khoản (nếu bật `LIQUIDITY_FOCUS_ENABLED` và ngoài khung giờ mạnh)
  - Lý do skip gần nhất (`last_skip_reason_by_symbol`)
- Điều kiện drift entry đang áp dụng:
  - Trần drift mặc định `ENTRY_DRIFT_MAX_PCT=0.60` (đơn vị `%`).
  - Nếu tín hiệu có `SL`, bot dùng ngưỡng động: `min(ENTRY_DRIFT_MAX_PCT, risk_pct * ENTRY_DRIFT_RISK_FRACTION)`.
  - Mặc định `ENTRY_DRIFT_RISK_FRACTION=0.60`; riêng XAU/XAUT/GOLD bật `XAU_GOLD_PROTECTION_ENABLED=true` sẽ chặn thêm bằng `XAU_GOLD_MAX_ENTRY_DRIFT_PCT=0.25`.
  - Bot skip khi `drift_pct > drift_limit_pct` (lớn hơn, không phải lớn hơn hoặc bằng).
- Guard riêng cho XAU/XAUT/GOLD (mặc định bật) để giảm lỗ do nhiễu/đuổi giá:
  - Chặn tín hiệu dưới `XAU_GOLD_MIN_INTERVAL_MINUTES=15` phút (loại 5m).
  - Chỉ cho tối đa `XAU_GOLD_MAX_ACTIVE_ORDERS=1` lệnh XAU đang mở.
  - Yêu cầu quality cao hơn ngưỡng chung thêm `XAU_GOLD_MIN_QUALITY_BONUS=0.35`.
  - Yêu cầu `XAU_GOLD_MIN_RR=1.30` và `XAU_GOLD_MIN_NET_RR_AFTER_FEES=0.85`.
  - Mặc định chặn fallback/grid cho XAU bằng `XAU_GOLD_BLOCK_FALLBACK=true`, `XAU_GOLD_BLOCK_GRID=true`.
- Tần suất noti trạng thái chờ:
  - Giới hạn theo từng symbol (mỗi symbol tối đa 1 lần/giờ khi không có lệnh mở).
  - Nếu chạy nhiều symbol, noti có thể xuất hiện gần nhau theo phút nhưng khác symbol.

## 4) Quy ước khi thay đổi code (cho Codex)
- Nếu sửa wording/format noti: cập nhật lại mục **3) Notification contract** trong file này.
- Nếu sửa logic vào lệnh/thoát lệnh: cập nhật mục **2) Luồng bot hiện tại**.
- Nếu thêm biến cấu hình mới ảnh hưởng noti: ghi rõ tên biến và tác động.
- Mỗi thay đổi nên tăng `Context version` theo dạng: `vYYYY.MM.DD-N`.

## 5) Checklist trước khi commit
- [ ] Không làm đổi logic trade ngoài phạm vi yêu cầu.
- [ ] `python -m py_compile bot.py` pass.
- [ ] Nếu đổi noti, kiểm tra độ dài message và tính dễ đọc.
- [ ] Cập nhật file `CODEX_CONTEXT.md` nếu có thay đổi hành vi.

## 6) Gợi ý cho prompt tương lai
Khi yêu cầu Codex chỉnh bot, nên ghi rõ:
- Cặp symbol/nguồn dữ liệu
- Kênh nhận noti
- Mẫu noti mong muốn (ví dụ cụ thể)
- Phần nào chỉ sửa UI text và phần nào được phép sửa logic
