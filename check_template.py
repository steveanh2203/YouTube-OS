import pyautogui
from pathlib import Path

def check_template():
    template_path = "resources/share_button_template.png"
    if not Path(template_path).exists():
        print(f"Error: {template_path} not found.")
        return

    print("Searching for template on screen...")
    try:
        # Confidence 0.8 to allow slight rendering differences
        location = pyautogui.locateOnScreen(template_path, confidence=0.8)
        if location:
            print(f"✅ Found template at: {location}")
        else:
            print("❌ Template not found on screen.")
    except Exception as e:
        print(f"Error searching for template: {e}")
        # Requires opencv for confidence
        print("Note: 'confidence' parameter requires opencv-python.")

if __name__ == "__main__":
    check_template()
