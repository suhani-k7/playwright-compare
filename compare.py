from PIL import Image, ImageChops, ImageDraw, ImageFont
import numpy as np
import os
from scipy import ndimage
from scipy.signal import correlate2d

os.makedirs("diffs", exist_ok=True)

ref = Image.open("reference/screen.png").convert("RGB")
live = Image.open("live/screen.png").convert("RGB")

if ref.size != live.size:
    live = live.resize(ref.size, Image.LANCZOS)
    print(f"Resized live image to match reference: {ref.size}")

# --- Pixel diff ---
diff = ImageChops.difference(ref, live)
diff_arr = np.array(diff)
changed_mask = np.any(diff_arr > 10, axis=2)

# --- Find regions ---
struct = ndimage.generate_binary_structure(2, 2)
dilated = ndimage.binary_dilation(changed_mask, structure=struct, iterations=4)
labeled, num_features = ndimage.label(dilated)

print(f"Detected {num_features} changed region(s)")


# --- Classification logic (priority-based, no overlap) ---
def classify_region(ref_crop, live_crop, region_w, region_h, shift=None):
    ref_arr = np.array(ref_crop).astype(np.float32)
    live_arr = np.array(live_crop).astype(np.float32)
    diff = np.abs(ref_arr - live_arr)

    avg_diff = diff.mean()
    changed_ratio = (np.any(diff > 10, axis=2).sum()) / (region_w * region_h)
    ref_mean = ref_arr.mean(axis=(0, 1))
    live_mean = live_arr.mean(axis=(0, 1))
    color_shift = np.abs(ref_mean - live_mean).mean()
    diff_std = diff.std()
    ref_brightness = ref_arr.mean()
    live_brightness = live_arr.mean()
    brightness_diff = abs(ref_brightness - live_brightness)

    # --- Priority 1: confirmed layout shift from detect_shift ---
    # If shift detection already confirmed a positional match with low error,
    # trust it and return immediately — avoids conflicting labels.
    if shift is not None:
        sx, sy = shift
        if abs(sx) > 5 or abs(sy) > 5:
            return "Layout / position shift"

    # --- Priority 2: brightness jump = theme or visibility change ---
    if brightness_diff > 40:
        return "Brightness / theme change"

    # --- Priority 3: whole region changed color uniformly ---
    if changed_ratio > 0.85 and color_shift > 30:
        return "Color change"

    # --- Priority 4: majority changed + moderate color shift = style ---
    if changed_ratio > 0.6 and color_shift > 15:
        return "Background or style change"

    # --- Priority 5: text/content — uneven diff, smaller area changed ---
    # Checked before the generic layout-shift rule so that small high-variance
    # regions (e.g. a changed word) aren't mislabeled as layout shifts.
    if diff_std > 30 and changed_ratio < 0.5:
        return "Content / text change"

    # --- Priority 6: layout shift — uneven diff, significant area ---
    if diff_std > 20 and changed_ratio > 0.4:
        return "Layout / position shift"

    # --- Priority 7: very subtle ---
    if avg_diff < 20 and changed_ratio < 0.3:
        return "Minor pixel difference"

    return "Visual difference"


# --- Shift detection via FFT-based cross-correlation ---
def detect_shift(ref, live, x_min, y_min, x_max, y_max, padding=40):
    img_w, img_h = live.size

    search_x1 = max(x_min - padding, 0)
    search_y1 = max(y_min - padding, 0)
    search_x2 = min(x_max + padding, img_w)
    search_y2 = min(y_max + padding, img_h)

    ref_crop = np.array(
        ref.crop((x_min, y_min, x_max, y_max)).convert("L")
    ).astype(np.float32)
    search_area = np.array(
        live.crop((search_x1, search_y1, search_x2, search_y2)).convert("L")
    ).astype(np.float32)

    crop_h, crop_w = ref_crop.shape
    search_h, search_w = search_area.shape

    if search_h < crop_h or search_w < crop_w:
        return None

    # Normalise both patches to zero mean so correlation measures shape, not
    # absolute brightness — makes it robust to theme/color changes.
    ref_norm = ref_crop - ref_crop.mean()
    search_norm = search_area - search_area.mean()

    # Cross-correlate: peak position reveals the best-matching offset.
    # correlate2d with mode='valid' slides ref over the search area — O(n log n)
    # via internal FFT rather than O(n²) brute force.
    correlation = correlate2d(search_norm, ref_norm, mode="valid")

    if correlation.size == 0:
        return None

    best_idx = np.unravel_index(np.argmax(correlation), correlation.shape)
    best_dy, best_dx = best_idx

    actual_x = search_x1 + best_dx
    actual_y = search_y1 + best_dy

    # Verify the match quality by comparing the aligned patch directly.
    aligned_patch = search_area[best_dy: best_dy + crop_h, best_dx: best_dx + crop_w]
    best_score = np.abs(aligned_patch - ref_crop).mean()

    shift_x = actual_x - x_min
    shift_y = actual_y - y_min

    if best_score < 15 and (abs(shift_x) > 3 or abs(shift_y) > 3):
        return shift_x, shift_y

    return None


# --- Draw boxes on live screenshot ---
annotated = live.copy()
draw = ImageDraw.Draw(annotated)

try:
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 11)
except Exception:
    print("Warning: Helvetica not found, falling back to default font. Labels may render small.")
    font = ImageFont.load_default()

BOX_COLOR = (255, 60, 60)
LABEL_BG  = (255, 60, 60)
LABEL_TEXT = (255, 255, 255)

placed_labels = []
change_count = 0


def overlaps(lx, ly, lw, lh, placed):
    """Check if a candidate label rect overlaps any already-placed label."""
    for (px, py, pw, ph) in placed:
        if not (lx + lw < px or lx > px + pw or ly + lh < py or ly > py + ph):
            return True
    return False


for i in range(1, num_features + 1):
    region = np.where(labeled == i)
    region_pixels = len(region[0])
    y_min, y_max = int(region[0].min()), int(region[0].max())
    x_min, x_max = int(region[1].min()), int(region[1].max())

    region_w = x_max - x_min
    region_h = y_max - y_min
    bbox_pixels = max(region_w * region_h, 1)

    # bbox_coverage: how densely changed pixels fill this region's bounding box
    bbox_coverage = (region_pixels / bbox_pixels) * 100

    if region_w < 5 or region_h < 5:
        continue

    change_count += 1

    ref_crop  = ref.crop((x_min, y_min, x_max, y_max))
    live_crop = live.crop((x_min, y_min, x_max, y_max))

    shift = detect_shift(ref, live, x_min, y_min, x_max, y_max, padding=40)
    description = classify_region(ref_crop, live_crop, region_w, region_h, shift=shift)

    if shift:
        sx, sy = shift
        shift_str = (
            f"shifted {abs(sx)}px {'right' if sx > 0 else 'left'}, "
            f"{abs(sy)}px {'down' if sy > 0 else 'up'}"
        )
        description = f"{description} + {shift_str}"

    label = (
        f"#{change_count} "
        f"({region_pixels}px, {bbox_coverage:.1f}% fill) "
        f"— {description}"
    )

    # Draw bounding box
    draw.rectangle([x_min, y_min, x_max, y_max], outline=BOX_COLOR, width=3)

    # Place label above the box, shifting down if it overlaps a previous label
    bbox_text = draw.textbbox((0, 0), label, font=font)
    text_w = bbox_text[2] - bbox_text[0]
    text_h = bbox_text[3] - bbox_text[1]
    label_x = x_min
    label_y = max(y_min - text_h - 8, 0)
    label_w = text_w + 8
    label_h = text_h + 6

    attempts = 0
    while overlaps(label_x, label_y, label_w, label_h, placed_labels) and attempts < 20:
        label_y += label_h + 4
        attempts += 1

    placed_labels.append((label_x, label_y, label_w, label_h))

    draw.rectangle(
        [label_x, label_y, label_x + label_w, label_y + label_h],
        fill=LABEL_BG,
    )
    draw.text((label_x + 4, label_y + 3), label, fill=LABEL_TEXT, font=font)

    print(f"  Change #{change_count} at ({x_min},{y_min}) → ({x_max},{y_max}): {description}")

annotated.save("diffs/annotated.png")
print(f"\nAnnotated diff saved to diffs/annotated.png ({change_count} changes marked)")

# --- Raw diff (amplified for visibility) ---
arr = np.clip(diff_arr.astype(np.float32) * 5, 0, 255).astype(np.uint8)
Image.fromarray(arr).save("diffs/diff.png")

# --- Stats ---
total_pixels = changed_mask.shape[0] * changed_mask.shape[1]
changed_pixels = changed_mask.sum()
pct = (changed_pixels / total_pixels) * 100

print(f"Changed pixels: {changed_pixels} / {total_pixels} ({pct:.2f}%)")
if pct < 1:
    print("✅ Nearly identical — no major visual regression.")
elif pct < 5:
    print("⚠️  Minor differences detected.")
else:
    print("❌ Significant visual differences found.")