# SMC Bot – Codex Context (Single Source of Truth)

> Mục tiêu: Tổng hợp trạng thái code mới nhất để Codex có thể đọc nhanh và sửa đúng ngữ cảnh khi có thay đổi.

## 1) Version snapshot
- Context version: `v2026.06.10-2`
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

## 1c) Thay đổi tối ưu lợi nhuận (07/06/2026)
- **Learning guard chống lỗ kéo dài**: `learning.py` nay lưu thêm `loss_streak`, `win_streak`, `max_loss_streak`, `last_pnl` theo từng bucket `symbol|strategy|interval|side`.
- **Chặn combo hiệu suất kém**: nếu bucket đủ mẫu nhưng win-rate thấp, avg PnL âm hoặc đang có chuỗi lỗ, tín hiệu sẽ được gắn `learning_blocked` để quality gate bỏ qua thay vì tiếp tục vào cùng setup đang lỗ.
- **Penalty thích nghi**: các bucket có Bayesian win-rate thấp, stable avg PnL âm hoặc loss streak sẽ bị trừ quality mạnh hơn trước; RR cũng giảm/nâng nhẹ theo expectancy đã làm mượt.
- **Config mới**: `LEARNING_BLOCK_BAD_COMBOS`, `LEARNING_BAD_COMBO_MIN_TRADES`, `LEARNING_BAD_COMBO_MAX_WIN_RATE`, `LEARNING_BAD_COMBO_MIN_AVG_PNL`, `LEARNING_LOSS_STREAK_BLOCK`, `LEARNING_MAX_QUALITY_PENALTY`.

## 1d) Thay đổi decision TF đa khung (07/06/2026)
- **Decision TF mặc định mở rộng**: `DECISION_INTERVALS` mặc định từ `5m,15m` thành `5m,15m,1h,4h,1d` để tín hiệu quyết định lấy bối cảnh đa khung.
- **Chống tự xác nhận**: khi boost/penalty quality cho candidate, bot bỏ qua decision signal trùng chính `interval` của candidate để tránh 1h/4h/1d tự cộng điểm cho nó.
- **Trọng số theo TF**: decision context dùng trọng số nhẹ cho 5m/15m và mạnh hơn cho 1h/4h/1d; đồng thuận tăng quality có trần, xung đột bị phạt mạnh hơn để ưu tiên setup cùng hướng đa khung.

## 1e) Thay đổi Telegram command menu (10/06/2026)
- **New**: Bot tự đăng ký menu lệnh Telegram qua `setMyCommands` khi có `TELEGRAM_TOKEN`. Các lệnh gồm `/start`, `/help`, `/status`, `/positions`, `/balance`, `/dashboard`.
- **New**: Thêm polling `getUpdates` trong thread nền để trả lời lệnh vận hành từ đúng `TELEGRAM_CHAT_ID`; có thể tắt bằng `TELEGRAM_COMMANDS_ENABLED=false`.
- **Config mới**: `PUBLIC_BASE_URL` dùng để tạo link `/dashboard` công khai; nếu chưa cấu hình, lệnh `/dashboard` sẽ hướng dẫn đặt URL.

## 1f) Tăng tần suất lệnh cân bằng (10/06/2026)
- **Fix sparse orders**: `MIN_TRADE_INTERVAL_MINUTES` mặc định giảm từ `60` xuống `15`, và `SCALP_INTERVALS` mặc định đổi thành `15m,30m,1h` để bot không còn chỉ trade 1h khi dùng cấu hình mặc định.
- **Gold guard bớt nghẽn lệnh**: XAU/XAUT/GOLD mặc định cho phép setup từ `15m`, tối đa `2` lệnh đang mở, bonus quality giảm còn `0.15`, net RR sau phí tối thiểu giảm còn `0.75`.
- **Liquidity filter bớt chặn ngoài giờ**: cửa sổ thanh khoản VN mặc định mở rộng `13-23`, soft RR tối thiểu còn `1.20`, soft quality tối thiểu còn `2.10`.
- **Fallback strict bớt kén**: `FALLBACK_MIN_QUALITY_SCORE` mặc định giảm còn `2.25` để strict engine có thêm cơ hội dùng tín hiệu backtest_v5 chất lượng vừa.

## 1g) Bổ sung nhịp vào lệnh khi bot quá ít lệnh (11/06/2026)
- **More active defaults**: `MIN_TRADE_INTERVAL_MINUTES` mặc định giảm còn `5`, `SCALP_INTERVALS` mặc định thêm `5m` (`5m,15m,30m,1h`) để có thêm setup nhưng vẫn tránh `1m/3m`.
- **Quality gate bớt kén**: `MIN_SIGNAL_QUALITY_SCORE` còn `1.85`, `SCALP_MIN_QUALITY_SCORE` còn `1.65`, `FALLBACK_MIN_QUALITY_SCORE` còn `2.05`.
- **Gold guard mở hơn nhưng còn bảo vệ**: XAU/XAUT/GOLD cho phép TF từ `5m`, tối đa `3` lệnh, bonus quality còn `0.05`, RR tối thiểu `1.15`, net RR sau phí `0.65`, entry drift tối đa `0.35`, và không chặn fallback mặc định.
- **Liquidity/cooldown bớt nghẽn**: fallback không bắt buộc high-liquidity, cooldown mặc định `120s`, ngoài giờ thanh khoản soft gate còn RR `1.05`, quality `1.90`, low-liquidity vẫn cho tối thiểu `2` lệnh.

## 2) Luồng bot hiện tại (rút gọn)
1. Lấy dữ liệu giá/khung thời gian.
2. Sinh tín hiệu SMC theo các TF theo dõi.
3. Nếu có tín hiệu đạt điều kiện => gửi noti tín hiệu + vào lệnh (nếu bật trade).
4. Nếu chưa có tín hiệu và không có lệnh mở => gửi noti trạng thái theo chu kỳ.
5. Candidate được điều chỉnh thêm bằng decision context đa khung (`DECISION_INTERVALS`, mặc định `5m,15m,1h,4h,1d`) và trend context toàn bộ TF theo dõi trước khi qua quality gate.
6. Trước khi vào lệnh, learning guard có thể giảm điểm hoặc chặn các bucket `symbol|strategy|interval|side` đang có thống kê lỗ/chuỗi lỗ.
7. Khi có vị thế mở => đồng bộ vị thế, theo dõi PnL, quản trị TP/SL, đóng vòng lệnh.

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

### 3.2 Telegram command menu
- Bot gọi `setMyCommands` ở startup để menu lệnh hiện trong Telegram.
- Lệnh hỗ trợ:
  - `/start`, `/help`: hướng dẫn nhanh và danh sách lệnh.
  - `/status`: mode, engine, symbol, TF theo dõi/quyết định, trạng thái thanh khoản, giá và lý do chờ/skip gần nhất.
  - `/positions`: các vị thế/lệnh bot đang theo dõi, entry, quantity và PnL tạm tính nếu lấy được giá.
  - `/balance`: số dư VST nếu có credential BingX; nếu không thì dùng snapshot startup.
  - `/dashboard`: trả link dashboard theo `PUBLIC_BASE_URL`, hoặc hướng dẫn cấu hình nếu biến này trống.
- Bot chỉ trả lời lệnh từ `TELEGRAM_CHAT_ID` khi biến này được cấu hình; có thể tắt polling lệnh bằng `TELEGRAM_COMMANDS_ENABLED=false`.

### 3.3 Noti lý do chờ (main loop)
- `wait_reason` hiện bao gồm:
  - Chưa có tín hiệu SMC mới ở TF theo dõi
  - Quality tối thiểu hiện tại (`MIN_SIGNAL_QUALITY_SCORE`)
  - Giới hạn lệnh mở (`current_max_active_orders`)
  - Ghi chú thanh khoản (nếu bật `LIQUIDITY_FOCUS_ENABLED` và ngoài khung giờ mạnh; mặc định `LIQUIDITY_WINDOWS_VN=13-23`)
  - Lý do skip gần nhất (`last_skip_reason_by_symbol`)
- Điều kiện drift entry đang áp dụng:
  - Trần drift mặc định `ENTRY_DRIFT_MAX_PCT=0.60` (đơn vị `%`).
  - Nếu tín hiệu có `SL`, bot dùng ngưỡng động: `min(ENTRY_DRIFT_MAX_PCT, risk_pct * ENTRY_DRIFT_RISK_FRACTION)`.
  - Mặc định `ENTRY_DRIFT_RISK_FRACTION=0.60`; riêng XAU/XAUT/GOLD bật `XAU_GOLD_PROTECTION_ENABLED=true` sẽ chặn thêm bằng `XAU_GOLD_MAX_ENTRY_DRIFT_PCT=0.25`.
  - Bot skip khi `drift_pct > drift_limit_pct` (lớn hơn, không phải lớn hơn hoặc bằng).
- Guard riêng cho XAU/XAUT/GOLD (mặc định bật) để giảm lỗ do nhiễu/đuổi giá:
  - Chặn tín hiệu dưới `XAU_GOLD_MIN_INTERVAL_MINUTES=15` phút (loại 5m).
  - Chỉ cho tối đa `XAU_GOLD_MAX_ACTIVE_ORDERS=2` lệnh XAU đang mở.
  - Yêu cầu quality cao hơn ngưỡng chung thêm `XAU_GOLD_MIN_QUALITY_BONUS=0.15`.
  - Yêu cầu `XAU_GOLD_MIN_RR=1.30` và `XAU_GOLD_MIN_NET_RR_AFTER_FEES=0.75`.
  - Mặc định chặn fallback/grid cho XAU bằng `XAU_GOLD_BLOCK_FALLBACK=true`, `XAU_GOLD_BLOCK_GRID=true`.
- Tần suất noti trạng thái chờ:
  - Giới hạn theo từng symbol (mỗi symbol tối đa 1 lần/giờ khi không có lệnh mở).
  - Nếu chạy nhiều symbol, noti có thể xuất hiện gần nhau theo phút nhưng khác symbol.

## 4) Quy ước khi thay đổi code (cho Codex)
- Nếu sửa wording/format noti hoặc command Telegram: cập nhật lại mục **3) Notification contract** trong file này.
- Nếu sửa logic vào lệnh/thoát lệnh: cập nhật mục **2) Luồng bot hiện tại**.
- Nếu thêm biến cấu hình mới ảnh hưởng noti: ghi rõ tên biến và tác động.
- Nếu thêm biến cấu hình mới ảnh hưởng vào/thoát lệnh: cập nhật mục thay đổi và luồng bot.
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
