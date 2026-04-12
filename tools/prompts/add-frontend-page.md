# Prompt: Thêm Frontend Page Mới

Dùng khi muốn thêm một page/view mới vào React frontend.

---

## Prompt Template

```
Thêm một page mới vào frontend AutoCapCut.

Tên page: [TÊN PAGE]
View name (string dùng trong Zustand): "[view-name]"

Chức năng:
- [Mô tả page làm gì]

UI cần:
- [Mô tả layout, các thành phần chính]
- [Có cần list/table/form/chart không?]

API calls:
- [Gọi endpoint nào? GET/POST gì?]

State:
- [Có cần state local (useState) hay dùng Zustand?]

Dùng /ui-ux-pro-max để design layout nếu chưa rõ hướng đi.

Checklist:
1. Tạo frontend/src/pages/[PageName]/index.tsx
2. Thêm "[view-name]" vào type MainView trong app.store.ts
3. Import và render trong WorkspacePanel
4. Thêm navigation button vào TopBar nếu cần
5. Dùng Radix UI cho interactive elements (không tự build)
6. Dùng Tailwind classes, không CSS modules
7. Dùng console.error() cho error handling
```

---

## Ví Dụ Điền

```
Thêm một page mới vào frontend AutoCapCut.

Tên page: Upload Queue
View name: "upload-queue"

Chức năng:
- Hiển thị danh sách video đang chờ upload lên YouTube
- Cho phép cancel, retry từng item

UI cần:
- Table/list với cột: thumbnail, title, status, progress bar
- Button cancel và retry per row
- Badge màu cho status (pending/uploading/done/failed)

API calls:
- GET /api/roxy/queue — lấy danh sách
- DELETE /api/roxy/queue/{id} — cancel

State:
- Zustand task.store.ts để track progress realtime

Dùng /ui-ux-pro-max để design layout nếu chưa rõ hướng đi.
```
