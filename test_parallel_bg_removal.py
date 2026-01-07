#!/usr/bin/env python3
"""Test parallel background removal with quality verification."""
from pathlib import Path
import time
from autocapcut.services.background_removal import remove_white_background

def test_parallel_processing():
    """Test parallel background removal."""
    print("🧪 Testing Parallel Background Removal\n")
    print("=" * 70)

    # Check if test images exist
    test_folder = Path("test_images")
    if not test_folder.exists():
        print("❌ Test folder 'test_images' does not exist")
        print("   Create it and add some test images first!")
        print("\n💡 You can create test images folder with:")
        print("   mkdir test_images")
        print("   # Add some JPG/PNG images to test_images/")
        return

    # Count images
    image_count = len(list(test_folder.glob("*.jpg"))) + \
                  len(list(test_folder.glob("*.jpeg"))) + \
                  len(list(test_folder.glob("*.png")))

    if image_count == 0:
        print("❌ No images found in test_images folder")
        print("   Add some JPG/PNG images first!")
        return

    print(f"📁 Test folder: {test_folder}")
    print(f"🖼️  Found {image_count} image(s)")
    print()

    # Test with different worker counts
    for workers in [1, 2, 4]:
        print(f"\n{'='*70}")
        print(f"🚀 Testing with {workers} worker(s) (mode=auto)")
        print(f"{'='*70}")

        start_time = time.time()

        try:
            summary = remove_white_background(
                test_folder,
                mode="auto",
                delete_original=False,  # IMPORTANT: Don't delete for testing
                max_workers=workers,
            )

            elapsed = time.time() - start_time

            print(f"\n✅ Completed in {elapsed:.2f}s")
            print(f"   Processed: {summary.processed}")
            print(f"   Background removed: {summary.removed_background}")
            print(f"   Kept: {summary.kept}")
            print(f"   Converted to PNG: {summary.converted_to_png}")
            print(f"   Errors: {len(summary.errors)}")
            print(f"   Mode used: {summary.mode_used}")
            print(f"   Speed: {summary.processed/elapsed:.2f} images/sec")

            if summary.errors:
                print(f"\n⚠️  Errors:")
                for error in summary.errors:
                    print(f"   - {error}")

        except Exception as e:
            elapsed = time.time() - start_time
            print(f"\n❌ Failed after {elapsed:.2f}s: {e}")

    print("\n" + "=" * 70)
    print("✨ Testing complete!")
    print("=" * 70)
    print("\n📊 Quality Verification:")
    print("   1. Check test_images/ folder for processed PNG files")
    print("   2. Compare with originals (should be identical quality)")
    print("   3. Verify background removal is accurate")
    print("   4. Original files should still exist (delete_original=False)")
    print("\n💡 If everything looks good, the parallel processing is working!")
    print("   Expected speedup with 4 workers: 3-4x faster than 1 worker")

if __name__ == "__main__":
    test_parallel_processing()
