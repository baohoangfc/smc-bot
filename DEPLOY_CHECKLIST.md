# Railway không build lại sau khi merge `main` — Checklist nhanh

## 1) Xác nhận commit đã có trên GitHub
```bash
git checkout main
git pull --ff-only
git log --oneline -n 5
```

## 2) Ép trigger một deploy mới
Tạo commit nhỏ và push trực tiếp lên `main`:

```bash
echo "# deploy trigger $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> .deploy-trigger
git add .deploy-trigger
git commit -m "chore: trigger Railway redeploy"
git push origin main
```

## 3) Kiểm tra cấu hình Railway
- Service → **Settings** → **Source**
  - Đúng GitHub repo
  - Đúng branch: `main`
- **Auto Deploy**: bật

## 4) Nếu vẫn không chạy
- Mở **Deploy Logs** kiểm tra có webhook `push` mới không.
- Vào GitHub repo → **Settings** → **Webhooks** (Railway) xem có lỗi delivery (4xx/5xx) không.

## 5) Cách xử lý tạm thời
- Bấm **Redeploy** thủ công trong Railway để đảm bảo production cập nhật ngay.
