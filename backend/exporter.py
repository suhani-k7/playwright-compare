import os
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMPARISONS_DIR = PROJECT_ROOT / "comparisons"

def bake_annotated_image(run_id: str, viewport: str, section: str) -> str:
    """
    On-demand draws bounding boxes and labels onto the live screenshot.
    Saves it as 'live-annotated.png' and returns its relative static path.
    """
    section_dir = COMPARISONS_DIR / run_id / viewport / section
    live_img_path = section_dir / "live.png"
    diff_json_path = section_dir / "diff.json"

    if not live_img_path.exists():
        raise FileNotFoundError(f"Live screenshot not found at {live_img_path}")
    if not diff_json_path.exists():
        raise FileNotFoundError(f"Diff results not found at {diff_json_path}")

    # Load live image
    try:
        img = Image.open(live_img_path)
    except Exception as e:
        raise RuntimeError(f"Failed to open live screenshot: {e}")

    # Load issues
    with open(diff_json_path, "r", encoding="utf-8") as f:
        diff_data = json.load(f)
    issues = diff_data.get("issues", [])

    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=16)
    except Exception:
        font = ImageFont.load_default()

    # Draw boxes
    for issue in issues:
        bbox = issue.get("boundingBox")
        if not bbox:
            continue  # Skip non-visual warnings like SEO changes

        x = bbox.get("x", 0)
        y = bbox.get("y", 0)
        w = bbox.get("width", 0)
        h = bbox.get("height", 0)

        # Skip elements that have invalid dimensions
        if w <= 0 or h <= 0:
            continue

        label = issue.get("type", "mismatch").replace("-", " ").title()
        elem = issue.get("element", "")
        if elem:
            label = f"{label} ({elem})"

        # Draw red outline
        draw.rectangle([(x, y), (x + w, y + h)], outline="red", width=3)

        # Draw text label background
        text_y = max(0, y - 20)
        label_text = label[:40] + "..." if len(label) > 43 else label
        try:
            text_bbox = draw.textbbox((x, text_y), label_text, font=font)
            # Ensure label fits within the image boundary horizontally
            label_w = text_bbox[2] - text_bbox[0]
            if x + label_w > img.width:
                x_shifted = max(0, img.width - label_w)
                text_bbox = draw.textbbox((x_shifted, text_y), label_text, font=font)
                draw.rectangle(text_bbox, fill="red")
                draw.text((x_shifted, text_y), label_text, fill="white", font=font)
            else:
                draw.rectangle(text_bbox, fill="red")
                draw.text((x, text_y), label_text, fill="white", font=font)
        except AttributeError:
            # Fallback for older Pillow versions
            draw.text((x, text_y), label_text, fill="red", font=font)

    # Save to disk
    annotated_path = section_dir / "live-annotated.png"
    img.save(annotated_path)

    # Return relative URL
    return f"/comparisons/{run_id}/{viewport}/{section}/live-annotated.png"
