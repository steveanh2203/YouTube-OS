# AutoCapcut Prototype (macOS)

Giai đoạn này tập trung vào **Sync Audio**: phân phối lại thời lượng ảnh/video trong CapCut draft dựa trên độ dài audio mà không cần mở CapCut.

## Prerequisites
- Python 3.11+ (đã thử với 3.12)
- FFmpeg/ffprobe khả dụng trong PATH (dùng để đo độ dài audio chính xác hơn)
- Bộ project CapCut nằm tại `~/Movies/CapCut/User Data/Projects`
- Sao lưu project trước khi thử nghiệm (tool sẽ tạo `.bak` cho `draft_content.json`).

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage
```bash
source .venv/bin/activate
python main.py
```

1. Nhấn **Reload Projects** để đọc lại danh sách project.
2. Tick các project muốn đồng bộ ảnh với audio.
3. Bấm **Sync Audio**. Tool sẽ cập nhật `draft_content.json` cho từng project, phân bổ lại thời lượng ảnh theo tổng thời gian track audio chính.
4. Nếu cần dừng giữa chừng, bấm **Stop**.
5. Mở CapCut và reload project – timeline sẽ phản ánh các thay đổi.

## Cách hoạt động
- `autocapcut/services/sync_audio.py` đọc file `draft_content.json`, tìm track `video` và `audio`, tính tổng thời gian audio rồi chia đều cho các segment video.
- Tool ghi đè `target_timerange`/`source_timerange` cho mỗi segment và cập nhật `duration` của project.
- Một bản backup `draft_content.json.bak` được tạo tự động nếu chưa tồn tại.

## Ghi chú
- Thuật toán hiện tại chia đều tất cả clip video theo tổng độ dài audio. Nếu bạn cần logic khác (theo beat, bỏ qua vài clip…), mở rộng trong `sync_audio.py`.
- Sync diễn ra offline nên CapCut cần reload project sau khi đồng bộ.
