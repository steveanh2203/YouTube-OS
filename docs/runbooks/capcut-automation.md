# Runbook: CapCut Automation Setup

Automation features (cut, export, sync) điều khiển CapCut qua GUI.
Cần setup đúng để chạy được.

## Yêu Cầu

- **CapCut phải đang mở** trước khi gọi bất kỳ automation API nào
- **macOS Accessibility permissions** phải được cấp cho app chạy code
- CapCut projects phải nằm tại đúng đường dẫn mặc định

## Cấp Accessibility Permission

1. Mở **System Settings → Privacy & Security → Accessibility**
2. Thêm Terminal (hoặc IDE bạn dùng để chạy) vào danh sách
3. Nếu dùng Tauri app đã build: thêm app đó vào danh sách

Kiểm tra permission:
```python
# Chạy trong Python shell
from atomacos import getAppRefByBundleId
app = getAppRefByBundleId('com.lveditor.CapCut')
print(app)  # Nếu lỗi → chưa có permission
```

## CapCut Project Location

Mặc định trong `config.py`:
```
~/Movies/CapCut/User Data/Projects/com.lveditor.draft/
```

Nếu CapCut của bạn lưu ở chỗ khác, chỉnh `APP_CONFIG.project_root` trong `autocapcut/config.py`.

## Mock Mode (Dev không cần CapCut)

Khi dev features không liên quan đến automation, bật mock mode:
```python
# autocapcut/config.py
APP_CONFIG = CapCutConfig(
    project_root=...,
    mock_mode=True  # Bỏ qua GUI calls thực
)
```

Hoặc set env var:
```bash
AUTOCAPCUT_MOCK=true ./scripts/run_api.sh
```

## Troubleshooting

**Lỗi `AXError` hoặc `cannot find element`:**
- CapCut chưa mở, hoặc chưa load xong
- Thêm delay: tăng `focus_delay_sec` / `menu_delay_sec` trong `CapCutConfig`

**Lỗi `permission denied` từ AtomAcos:**
- Chưa cấp Accessibility permission (xem bước trên)
- Thử restart Terminal sau khi cấp permission

**Automation nhấn nhầm chỗ / export sai:**
- CapCut đã update UI và layout thay đổi
- Kiểm tra `automation/capcut_automation.py` — có thể cần update selectors

**Timeout khi mở project (`project_open_timeout_sec`):**
- Project CapCut quá lớn
- Tăng `project_open_timeout_sec` trong `CapCutConfig` (mặc định 180s)

## Các Constants Quan Trọng

```python
# autocapcut/config.py
capcut_launch_timeout_sec: int = 60
project_open_timeout_sec: int = 180
export_dialog_timeout_sec: int = 30
render_timeout_sec: int = 900  # 15 phút cho video dài
```
