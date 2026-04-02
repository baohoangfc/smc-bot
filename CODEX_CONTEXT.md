# SMC Bot – Codex Context (Single Source of Truth)

> Mục tiêu: Tổng hợp trạng thái code mới nhất để Codex có thể đọc nhanh và sửa đúng ngữ cảnh khi có thay đổi.

## 1) Version snapshot
- Context version: `v2026.04.01-1`
- Repo chính hiện tại: `smc-bot`
- File runtime cốt lõi: `bot.py`
- Các file hỗ trợ: `requirements.txt`, `Dockerfile`, `railway.toml`, `SMC_AUDIT.md`

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
  - Trần drift mặc định `ENTRY_DRIFT_MAX_PCT=0.30` (đơn vị `%`).
  - Nếu tín hiệu có `SL`, bot dùng ngưỡng động: `min(ENTRY_DRIFT_MAX_PCT, risk_pct * ENTRY_DRIFT_RISK_FRACTION)`.
  - Mặc định `ENTRY_DRIFT_RISK_FRACTION=0.30` để độ lệch entry bám theo độ rộng setup (gần logic invalidation của SMC hơn ngưỡng cứng thuần túy).
  - Bot skip khi `drift_pct > drift_limit_pct` (lớn hơn, không phải lớn hơn hoặc bằng).
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
