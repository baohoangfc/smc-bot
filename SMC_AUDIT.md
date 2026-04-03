# SMC Audit (01-04-2026)

## Kết luận nhanh
Bot **đang dùng SMC ở mức bán phần (SMC-lite / hybrid)**, **chưa phải SMC chuẩn đầy đủ theo ICT/SMC cổ điển**.

## Những gì bot đã làm đúng theo tinh thần SMC
1. Có phát hiện **liquidity sweep** dựa trên quét swing high/low gần nhất.
2. Có dùng **MSS (market structure shift)** kiểu close vượt high/low nến trước.
3. Có lọc **premium/discount** bằng midpoint của dealing range gần.
4. Có ưu tiên lệnh theo hướng bias (EMA + trend) để giảm trade ngược xu hướng.

## Điểm chưa chuẩn SMC (thiếu hoặc giản lược)
1. **Structure chưa tách rõ HTF/LTF chuẩn SMC**:
   - Bias đang phụ thuộc EMA50/EMA200 thay vì BOS/CHOCH đa khung thời gian.
2. **MSS định nghĩa đơn giản**:
   - Chỉ dùng close so với high/low nến liền trước, chưa dùng pivot structure đủ ý nghĩa.
3. **Liquidity model còn mỏng**:
   - Mới xét 1 cụm swing gần, chưa có external vs internal liquidity rõ ràng.
4. **Order Block/FVG chưa phải lõi của engine scalp**:
   - Có logic OB trong backtest_v5, nhưng engine chính vẫn có nhánh fallback/smc_lite không bắt buộc OB/FVG mitigation.
5. **Risk engine thiên về ATR/EMA**, không phải execution model SMC chuẩn (entry theo vùng POI + invalidation theo cấu trúc rõ ràng).

## Mức độ tuân thủ ước lượng
- SMC core concepts: **~55-65%**
- ICT-style execution strictness: **~40-50%**

=> Dùng được cho giao dịch thực dụng, nhưng gọi là "SMC chuẩn xác" thì **chưa**.

## Đề xuất nâng cấp để gần "SMC chuẩn" hơn
1. Xây module structure rõ ràng:
   - Xác định BOS/CHOCH từ swing pivot (không dùng EMA làm điều kiện chính).
2. Tách HTF/LTF workflow:
   - HTF xác định bias + external liquidity.
   - LTF chờ sweep + displacement + MSS + retest POI.
3. Chuẩn hóa POI:
   - Bắt buộc có OB hoặc FVG hợp lệ trước entry.
4. Chuẩn hóa invalidation:
   - SL theo invalidation cấu trúc (beyond OB/FVG + liquidity) thay vì chủ yếu ATR.
5. Thêm logging lý do vào/không vào theo checklist SMC để dễ audit hiệu năng.
