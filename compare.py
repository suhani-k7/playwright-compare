from PIL import Image, ImageChops, ImageDraw, ImageFont
import numpy as np
import os
from scipy import ndimage

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

# --- Classification logic ---
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

    change_types = []

    # --- Rule 1: shift detected = layout shift, don't override with text ---
    if shift is not None:
        sx, sy = shift
        if abs(sx) > 5 or abs(sy) > 5:
            change_types.append("Layout / position shift")

    # --- Rule 2: whole region changed color uniformly ---
    if changed_ratio > 0.85 and color_shift > 30:
        change_types.append("Color change")

    # --- Rule 3: brightness jumped = theme or visibility change ---
    if brightness_diff > 40:
        change_types.append("Brightness / theme change")

    # --- Rule 4: majority changed + moderate color shift = style ---
    if changed_ratio > 0.6 and color_shift > 15:
        change_types.append("Background or style change")

    # --- Rule 5: layout shift — uneven diff, significant area ---
    # Check this BEFORE content/text so positional changes aren't mislabeled
    if diff_std > 20 and changed_ratio > 0.4:
        change_types.append("Layout / position shift")

    # --- Rule 6: text/content — uneven diff, smaller area changed ---
    if diff_std > 30 and changed_ratio < 0.5:
        change_types.append("Content / text change")

    # --- Rule 7: very subtle ---
    if avg_diff < 20 and changed_ratio < 0.3:
        change_types.append("Minor pixel difference")
    if not change_types:
        change_types.append("Visual difference")
    return ", ".join(change_types)

def detect_shift(ref, live, x_min, y_min, x_max, y_max, padding=40):
    img_w, img_h = live.size

    search_x1 = max(x_min - padding, 0)
    search_y1 = max(y_min - padding, 0)
    search_x2 = min(x_max + padding, img_w)
    search_y2 = min(y_max + padding, img_h)

    ref_crop = np.array(ref.crop((x_min, y_min, x_max, y_max)).convert("L")).astype(np.float32)
    search_area = np.array(live.crop((search_x1, search_y1, search_x2, search_y2)).convert("L")).astype(np.float32)

    crop_h, crop_w = ref_crop.shape
    search_h, search_w = search_area.shape

    if search_h < crop_h or search_w < crop_w:
        return None

    best_score = float("inf")
    best_dx, best_dy = 0, 0

    step = 2
    for dy in range(0, search_h - crop_h, step):
        for dx in range(0, search_w - crop_w, step):
            patch = search_area[dy:dy+crop_h, dx:dx+crop_w]
            score = np.abs(patch - ref_crop).mean()
            if score < best_score:
                best_score = score
                best_dx = dx
                best_dy = dy

    actual_x = search_x1 + best_dx
    actual_y = search_y1 + best_dy
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
except:
    font = ImageFont.load_default()

BOX_COLOR   = (255, 60, 60)
LABEL_BG    = (255, 60, 60)
LABEL_TEXT  = (255, 255, 255)

placed_labels = []
change_count = 0

for i in range(1, num_features + 1):
    region = np.where(labeled == i)
    region_pixels = len(region[0])
    y_min, y_max = int(region[0].min()), int(region[0].max())
    x_min, x_max = int(region[1].min()), int(region[1].max())

    region_w = x_max - x_min
    region_h = y_max - y_min
    bbox_pixels = max(region_w * region_h, 1)
    region_pct = (region_pixels / bbox_pixels) * 100

    if region_w < 5 or region_h < 5:
        continue

    change_count += 1

    ref_crop  = ref.crop((x_min, y_min, x_max, y_max))
    live_crop = live.crop((x_min, y_min, x_max, y_max))

    shift = detect_shift(ref, live, x_min, y_min, x_max, y_max, padding=40)
    description = classify_region(ref_crop, live_crop, region_w, region_h, shift=shift)

    if shift:
        sx, sy = shift
        shift_str = f"shifted {abs(sx)}px {'right' if sx > 0 else 'left'}, {abs(sy)}px {'down' if sy > 0 else 'up'}"
        description = f"{description} + {shift_str}"

    label = (
        f"#{change_count} "
        f"({region_pixels}px, {region_pct:.1f}%) "
        f"— {description}"
    )

    # Draw box
    draw.rectangle([x_min, y_min, x_max, y_max], outline=BOX_COLOR, width=3)

    # Draw label
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    label_x = x_min
    label_y = max(y_min - text_h - 8, 0)

    # Shift label down until it doesn't overlap any previously placed label
    def overlaps(lx, ly, lw, lh, placed):
        for (px, py, pw, ph) in placed:
            if not (lx + lw < px or lx > px + pw or ly + lh < py or ly > py + ph):
                return True
        return False

    label_w = text_w + 8
    label_h = text_h + 6
    attempts = 0
    while overlaps(label_x, label_y, label_w, label_h, placed_labels) and attempts < 20:
        label_y += label_h + 4    # shift down by one label height
        attempts += 1

    placed_labels.append((label_x, label_y, label_w, label_h))

    draw.rectangle([label_x, label_y, label_x + label_w, label_y + label_h], fill=LABEL_BG)
    draw.text((label_x + 4, label_y + 3), label, fill=LABEL_TEXT, font=font)




    print(f"  Change #{change_count} at ({x_min},{y_min}) → ({x_max},{y_max}): {description}")

annotated.save("diffs/annotated.png")
print(f"\nAnnotated diff saved to diffs/annotated.png ({change_count} changes marked)")

# --- Raw diff ---
arr = np.array(diff_arr).astype(np.float32)
arr = np.clip(arr * 5, 0, 255).astype(np.uint8)
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