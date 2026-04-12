# Prompt: Thêm Python Service Mới

Dùng khi muốn thêm business logic mới vào `autocapcut/services/`.

---

## Prompt Template

```
Thêm một service mới vào autocapcut/services/.

Tên file: [feature_name].py
Chức năng: [Mô tả service làm gì]

Input:
- [Dữ liệu đầu vào, types]

Output:
- [Dữ liệu trả về, types]

Dependencies:
- [Cần dùng thư viện gì? FFmpeg? CapCut draft? DB?]
- [Tham khảo service tương tự: services/[file].py]

Constraints:
- macOS-only nếu dùng automation
- Không import từ api/ (services không depend FastAPI)
- Dùng loguru logger, không print()
- Type hints bắt buộc
- Raise custom Exception class nếu có lỗi domain-specific

Sau khi tạo service, nếu cần expose qua API:
→ Dùng prompt add-api-route.md
```

---

## Ví Dụ Điền

```
Thêm một service mới vào autocapcut/services/.

Tên file: thumbnail_generator.py
Chức năng: Tạo thumbnail từ frame của video, resize đúng tỷ lệ YouTube (16:9, 1280x720)

Input:
- video_path: Path — đường dẫn đến file video
- timestamp_sec: float — giây muốn capture frame (default: 0.0)

Output:
- thumbnail_path: Path — đường dẫn đến file PNG output

Dependencies:
- FFmpeg (ffmpeg-python hoặc subprocess)
- Tham khảo service tương tự: services/ffmpeg_utils.py

Constraints:
- Không cần automation (không macOS-only)
- Raise ThumbnailGenerationError nếu FFmpeg thất bại
```
