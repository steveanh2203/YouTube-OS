# Runbook: FFmpeg Debug

## Kiểm Tra FFmpeg Có Trong PATH

```bash
ffmpeg -version
ffprobe -version
which ffmpeg
```

Nếu chưa có:
```bash
brew install ffmpeg
```

## Kiểm Tra FFmpeg Từ Python

```python
import subprocess
result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
print(result.stdout)
```

## Log FFmpeg

Logs render nằm tại `logs/` trong project root. Kiểm tra file log mới nhất:

```bash
ls -t logs/ | head -5
cat logs/<file-moi-nhat>.log
```

## Lỗi Thường Gặp

**`ffmpeg: command not found` khi chạy qua API:**
- FFmpeg có trong PATH của terminal nhưng không có trong PATH của Python process
- Fix: Thêm full path vào service call, hoặc thêm vào `.env`:
  ```
  FFMPEG_BIN=/opt/homebrew/bin/ffmpeg
  FFPROBE_BIN=/opt/homebrew/bin/ffprobe
  ```

**Render timeout (> 900 giây):**
- Video quá dài hoặc resolution quá cao
- Chia nhỏ batch hoặc giảm độ phân giải đầu ra
- Kiểm tra timeout của service đang chạy job tương ứng

**Output video bị corrupt / không mở được:**
- Kiểm tra disk space: `df -h ~`
- Kiểm tra log để xem ffmpeg exit code

**Real-ESRGAN upscale chậm lần đầu:**
- Bình thường — binary đang được cached lần đầu
- Lần sau sẽ nhanh hơn (pre-warmed lúc API startup)

## Test Nhanh FFmpeg

```bash
# Tạo video test 5 giây
ffmpeg -f lavfi -i testsrc=duration=5:size=1280x720:rate=30 /tmp/test.mp4

# Kiểm tra info video
ffprobe -v quiet -print_format json -show_streams /tmp/test.mp4
```

## Render Status

Render jobs được track trong DB table `render_history`:
```bash
# Xem các render jobs gần đây
sqlite3 ~/.autocapcut/autocapcut.db \
  "SELECT id, status, started_at, finished_at, error_log FROM render_history ORDER BY id DESC LIMIT 10;"
```
