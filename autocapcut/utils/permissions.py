import subprocess
import Quartz
from loguru import logger

def check_screen_recording_permission() -> bool:
    """
    Check if the application has Screen Recording permission.
    
    Tries to capture a 1x1 pixel screenshot. If it fails or returns a window list
    that implies checking failed, we assume permission is denied.
    """
    try:
        # Attempt to capture a 1x1 pixel from the screen.
        # kCGWindowListOptionOnScreenOnly = 0x1
        # kCGNullWindowID = 0
        image = Quartz.CGWindowListCreateImage(
            Quartz.CGRectMake(0, 0, 1, 1),
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
            Quartz.kCGWindowImageDefault
        )
        return image is not None
    except Exception as e:
        logger.error(f"Error checking screen recording permission: {e}")
        return False

def open_screen_recording_settings():
    """
    Open macOS System Settings directly to the Screen Recording permission page.
    """
    try:
        # Deep link to Privacy & Security -> Screen Recording
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"])
    except Exception as e:
        logger.error(f"Failed to open system settings: {e}")
