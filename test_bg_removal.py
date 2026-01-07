#!/usr/bin/env python3
"""Test script for background removal feature."""
from pathlib import Path
from autocapcut.services.background_removal import remove_white_background

# Test the new background removal modes
def test_modes():
    """Test different background removal modes."""
    print("🧪 Testing Background Removal Modes\n")

    # Check if test images exist
    test_folder = Path("test_images")
    if not test_folder.exists():
        print("❌ Test folder 'test_images' does not exist")
        print("   Create it and add some test images first!")
        return

    print(f"📁 Test folder: {test_folder}")

    # Test Fast Mode
    print("\n" + "="*60)
    print("🚀 Testing FAST mode (white background only)")
    print("="*60)
    try:
        summary = remove_white_background(
            test_folder,
            mode="fast",
            delete_original=False,
        )
        print(f"✅ Fast mode completed:")
        print(f"   - Processed: {summary.processed}")
        print(f"   - Background removed: {summary.removed_background}")
        print(f"   - Kept: {summary.kept}")
        print(f"   - Errors: {len(summary.errors)}")
    except Exception as e:
        print(f"❌ Fast mode failed: {e}")

    # Test AI Mode
    print("\n" + "="*60)
    print("🤖 Testing AI mode (any background)")
    print("="*60)
    try:
        summary = remove_white_background(
            test_folder,
            mode="ai",
            delete_original=False,
        )
        print(f"✅ AI mode completed:")
        print(f"   - Processed: {summary.processed}")
        print(f"   - Background removed: {summary.removed_background}")
        print(f"   - Kept: {summary.kept}")
        print(f"   - Errors: {len(summary.errors)}")
        print(f"   - Mode used: {summary.mode_used}")
    except Exception as e:
        print(f"❌ AI mode failed: {e}")

    # Test Auto Mode
    print("\n" + "="*60)
    print("🎯 Testing AUTO mode (smart detection)")
    print("="*60)
    try:
        summary = remove_white_background(
            test_folder,
            mode="auto",
            delete_original=False,
        )
        print(f"✅ Auto mode completed:")
        print(f"   - Processed: {summary.processed}")
        print(f"   - Background removed: {summary.removed_background}")
        print(f"   - Kept: {summary.kept}")
        print(f"   - Errors: {len(summary.errors)}")
        print(f"   - Mode used: {summary.mode_used}")
    except Exception as e:
        print(f"❌ Auto mode failed: {e}")

    print("\n" + "="*60)
    print("✨ Testing complete!")
    print("="*60)

if __name__ == "__main__":
    test_modes()
