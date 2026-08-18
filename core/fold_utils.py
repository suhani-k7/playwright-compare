import os
import json
from PIL import Image, ImageChops

def _load_elements(elements_json_path):
    with open(elements_json_path, "r", encoding="utf-8") as f:
        return json.load(f)

def _collect_bottoms(elements_data):
    """
    Walk every category in elements.json and pull out each element's
    bbox bottom edge (y + height). Confirmed structure: every item has
    a nested "bbox": {"x", "y", "width", "height"} dict.
    """
    bottoms = []
    for category, items in elements_data.items():
        if not isinstance(items, list):
            continue
        for item in items:
            bbox = item.get("bbox")
            if not bbox:
                continue
            try:
                bottoms.append(bbox["y"] + bbox["height"])
            except (KeyError, TypeError):
                continue
    return sorted(bottoms)

def _pick_cut_points(bottoms, page_height, target_fold_height=800, snap_window=150):
    """
    Choose fold boundaries near every multiple of target_fold_height,
    snapped to the nearest element-bottom within snap_window px so we
    never cut through the middle of an element. Falls back to the
    raw target if nothing is nearby.
    """
    cuts = []
    y = target_fold_height
    while y < page_height:
        candidates = [b for b in bottoms if abs(b - y) <= snap_window]
        cuts.append(min(candidates, key=lambda b: abs(b - y)) if candidates else y)
        y += target_fold_height
    return cuts


def split_into_folds(screenshot_path, elements_json_path, out_dir,mode, device, slug, target_fold_height=800, repo_root=None):

    """
    Crops one full-page screenshot into folds, cutting at safe points
    derived from elements.json. Returns a list of dicts:
    [{"index": 0, "y_start": 0, "y_end": 812, "path": "...folds/0.png"}, ...]
    """
    img = Image.open(screenshot_path)
    width, page_height = img.size

    elements_data = _load_elements(elements_json_path)
    bottoms = _collect_bottoms(elements_data)
    cuts = [0] + _pick_cut_points(bottoms, page_height, target_fold_height) + [page_height]
    cuts = sorted(set(cuts))

    os.makedirs(out_dir, exist_ok=True)
    folds = []
    for i in range(len(cuts) - 1):
        y_start, y_end = cuts[i], cuts[i + 1]
        if y_end - y_start < 40:
            continue
        fold_img = img.crop((0, y_start, width, y_end))
        fold_path = os.path.join(out_dir, f"{mode}-fold{len(folds)}.png")
        fold_img.save(fold_path)

        stored_path = os.path.relpath(fold_path, repo_root) if repo_root else fold_path
        folds.append({
            "index": len(folds),
            "y_start": y_start,
            "y_end": y_end,
            "path": fold_path,        # absolute — used internally for diffing
            "web_path": stored_path,  # relative to repo root — used in JSON/frontend
        })
    return folds

def compute_fold_diff(reference_fold_path, live_fold_path):
    """
    Pure-PIL pixel diff, no numpy needed. Returns a diff percentage
    (0 = identical, 100 = completely different).
    """
    ref = Image.open(reference_fold_path).convert("RGB")
    live = Image.open(live_fold_path).convert("RGB")

    if ref.size != live.size:
        live = live.resize(ref.size)  # naive alignment; fine for a v1 demo

    diff = ImageChops.difference(ref, live).convert("L")
    thresholded = diff.point(lambda p: 255 if p > 25 else 0)  # 25 = noise threshold
    hist = thresholded.histogram()
    changed_pixels = sum(hist[1:])
    total_pixels = ref.size[0] * ref.size[1]
    return round((changed_pixels / total_pixels) * 100, 2)

def compare_visual_folds(reference_dir, live_dir, out_dir, device, slug,
                          target_fold_height=800, flag_threshold=5.0, repo_root=None):
    """
    Orchestrates: split both reference and live screenshots into folds,
    pair them by index, compute a diff score per pair.
    Returns (status, all_folds) — ALL folds are returned (not just
    flagged ones) so the frontend can render the full page in order;
    each fold carries a "flagged" bool for highlighting.
    """
    ref_screenshot = os.path.join(reference_dir, f"reference-{device}-{slug}-screenshot.png")
    live_screenshot = os.path.join(live_dir, f"live-{device}-{slug}-screenshot.png")
    ref_elements = os.path.join(reference_dir, f"reference-{device}-{slug}-elements.json")

    ref_folds = split_into_folds(ref_screenshot, ref_elements, out_dir,
                                  "reference", device, slug, target_fold_height, repo_root=repo_root)
    live_folds = split_into_folds(live_screenshot, ref_elements, out_dir,
                                   "live", device, slug, target_fold_height, repo_root=repo_root)

    all_folds = []
    flagged_count = 0
    for i in range(min(len(ref_folds), len(live_folds))):
        score = compute_fold_diff(ref_folds[i]["path"], live_folds[i]["path"])
        flagged = score >= flag_threshold
        flagged_count += flagged

        fold_result = {
            "type": "visual_fold_diff",
            "fold_index": i,
            "reference_image": ref_folds[i]["web_path"],
            "live_image": live_folds[i]["web_path"],
            "y_start": ref_folds[i]["y_start"],
            "y_end": ref_folds[i]["y_end"],
            "diff_percent": score,
            "flagged": flagged,
            "message": f"Fold {i}: {score}% visually different",
        }
        all_folds.append(fold_result)

    status = "PASS" if flagged_count == 0 else "FLAGGED"
    return status, all_folds