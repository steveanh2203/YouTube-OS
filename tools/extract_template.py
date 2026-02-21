from PIL import Image
from pathlib import Path

def extract_share_button(image_path, output_path):
    img = Image.open(image_path).convert("RGB")
    width, height = img.size
    pixels = img.load()
    
    # CapCut "Share" button is Cyan/Blue.
    # Relaxed Threshold
    start_x = int(width * 0.4) # Look a bit wider
    start_y = int(height * 0.4)
    
    points = []
    
    for y in range(start_y, height):
        for x in range(start_x, width):
            r, g, b = pixels[x, y]
            # Cyan/Blue-ish
            # R should be low. G and B should be high.
            if r < 120 and g > 150 and b > 180:
                if b > r + 40: # Ensure it's blue-dominant
                    points.append((x, y))

    if not points:
        print("No cyan pixels found!")
        return

    # Find bounds
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    
    w = max_x - min_x
    h = max_y - min_y
    
    print(f"Blue Area Bounds: {min_x},{min_y} to {max_x},{max_y}")
    print(f"Width: {w}, Height: {h}")
    
    if w > 400 or h > 300:
        print("Warning: Area still big. Attempting to isolate bottom-right cluster.")
        # Filter points that are in the last 20% of the bounding box?
        # The button is likely at the bottom right of the detected area.
        
        filtered_points = [p for p in points if p[0] > max_x - 150 and p[1] > max_y - 100]
        if filtered_points:
            f_xs = [p[0] for p in filtered_points]
            f_ys = [p[1] for p in filtered_points]
            min_x, max_x = min(f_xs), max(f_xs)
            min_y, max_y = min(f_ys), max(f_ys)
            w = max_x - min_x
            h = max_y - min_y
            print(f"Refined Bounds: {min_x},{min_y} to {max_x},{max_y} ({w}x{h})")
    
    crop = img.crop((min_x, min_y, max_x+1, max_y+1))
    
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(output_path)
    print(f"Saved to {output_path}")

if __name__ == "__main__":
    extract_share_button(
        "/Users/steveanh/.gemini/antigravity/brain/691966fb-f461-468d-aa94-71f28c93ec2f/uploaded_image_0_1768987921081.png",
        "resources/share_button_template.png"
    )
