# Prompt: Thêm API Route Mới

Dùng prompt này khi muốn thêm một FastAPI route mới vào AutoCapCut.

---

## Prompt Template

```
Thêm một FastAPI route mới cho feature: [TÊN FEATURE]

Chức năng:
- [Mô tả ngắn gọn feature làm gì]

Endpoints cần:
- [GET/POST/PUT/DELETE] /api/[prefix]/[path] — [mô tả]

Data cần:
- Input: [mô tả dữ liệu đầu vào]
- Output: [mô tả response]

Database:
- [Có cần đọc/ghi DB không? Bảng nào?]
- [Có cần thêm cột mới không?]

Tham khảo route tương tự: autocapcut/api/routes/[file tương tự].py

Checklist:
1. Tạo autocapcut/api/routes/[feature].py với router = APIRouter()
2. Thêm async handlers với Depends(get_session) nếu cần DB
3. Dùng loguru logger, không dùng print()
4. Register router trong autocapcut/api/server.py
5. Nếu cần cột DB mới: thêm vào _ensure_sqlite_columns() trong connection.py
```

---

## Ví Dụ Điền

```
Thêm một FastAPI route mới cho feature: Export batch SRT

Chức năng:
- Export nhiều SRT files cùng lúc từ danh sách child project IDs

Endpoints cần:
- POST /api/srt/batch-export — nhận list child_project_ids, trả về list SRT content

Data cần:
- Input: {"child_project_ids": [1, 2, 3]}
- Output: [{"child_project_id": 1, "srt": "...", "status": "ok"}, ...]

Database:
- Đọc bảng captions để lấy SRT đã có
- Không cần cột mới

Tham khảo route tương tự: autocapcut/api/routes/srt.py
```
