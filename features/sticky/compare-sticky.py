import argparse
import json
import os
import sys
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
import re


# Base directories for sticky module
STICKY_DIR = os.path.abspath(os.path.dirname(__file__))
REFERENCE_DIR = os.path.join(STICKY_DIR, "reference")
LIVE_DIR = os.path.join(STICKY_DIR, "live")
REPORTS_DIR = os.path.join(STICKY_DIR, "reports")
DIFFS_DIR = os.path.join(STICKY_DIR, "diffs")

# Ensure project root is on path for importing compare utilities
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "core"))
sys.path.insert(0, PROJECT_ROOT)

# Import comparison helpers
from compare import (
    compare_headings,
    compare_images,
    compare_buttons,
    compare_links,
    compare_canonical,
    compare_meta,
    compare_og_tags,
)

def load_html(mode: str, device: str, slug: str) -> BeautifulSoup:
    """Load the saved HTML for a given mode/device/slug."""
    base = REFERENCE_DIR if mode == "reference" else LIVE_DIR
    path = os.path.join(base, slug, device, f"{mode}-{device}-{slug}-page.html")
    if not os.path.exists(path):
        raise FileNotFoundError(f"HTML not found: {path}. Run capture-sticky.py first.")
    with open(path, "r", encoding="utf-8") as f:
        return BeautifulSoup(f.read(), "lxml")

def load_elements(mode: str, device: str, slug: str) -> dict:
    """Load the elements JSON for a given mode/device/slug."""
    base = REFERENCE_DIR if mode == "reference" else LIVE_DIR
    path = os.path.join(base, slug, device, f"{mode}-{device}-{slug}-elements.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Elements JSON not found: {path}. Run capture-sticky.py first.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def compare_sticky(ref_elements: dict, live_elements: dict):
    """Compare sticky elements between reference and live.
    Returns a tuple (status, issues) where issues contain detailed mismatches.
    """
    ref_sticky = ref_elements.get("sticky", [])
    live_sticky = live_elements.get("sticky", [])
    issues = []
    # Presence/Count check
    if (ref_sticky and not live_sticky) or (live_sticky and not ref_sticky):
        issues.append({"type": "sticky_presence", "message": "Sticky presence mismatch"})
    if len(ref_sticky) != len(live_sticky):
        issues.append({
            "type": "sticky_count",
            "ref_count": len(ref_sticky),
            "live_count": len(live_sticky),
            "message": f"Sticky count differs (ref={len(ref_sticky)} live={len(live_sticky)})"
        })
    # Detailed element comparison by tag and bbox coordinates
    def norm(el):
        b = el.get("bbox", {})
        return (
            el.get("tag", "").lower(),
            round(b.get("x", 0)),
            round(b.get("y", 0)),
            round(b.get("width", 0)),
            round(b.get("height", 0))
        )

    ref_set = {norm(e) for e in ref_sticky}
    live_set = {norm(e) for e in live_sticky}
    for missing in ref_set - live_set:
        # find original element to get bbox
        orig = next(e for e in ref_sticky if norm(e) == missing)
        issues.append({"type": "sticky_missing", "message": f"Missing sticky in live: {missing}", "bbox": orig.get("bbox")})
    for extra in live_set - ref_set:
        orig = next(e for e in live_sticky if norm(e) == extra)
        issues.append({"type": "sticky_extra", "message": f"Extra sticky in live: {extra}", "bbox": orig.get("bbox")})
    status = "PASS" if not issues else "FAIL"
    return status, issues

def _load_pct_elements(mode: str, device: str, slug: str, pct: int) -> dict:
    base = REFERENCE_DIR if mode == "reference" else LIVE_DIR
    path = os.path.join(base, slug, device, f"{mode}-{device}-{slug}-{pct}pct-elements.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _bboxes_at_pct(device: str, slug: str, pct: int) -> list:
    """All element bboxes present in the LIVE capture at this scroll pct,
    across every category — used to check whether a flagged element was
    actually on screen at this particular scroll position."""
    data = _load_pct_elements("live", device, slug, pct)
    boxes = []
    for cat in ["headings", "images", "buttons", "links", "sticky"]:
        for el in data.get(cat, []):
            b = el.get("bbox")
            if b:
                boxes.append(b)
    return boxes


def _bbox_present(bbox: dict, candidates: list, tol: int = 5) -> bool:
    for c in candidates:
        if (abs(bbox["x"] - c["x"]) <= tol and abs(bbox["y"] - c["y"]) <= tol and
            abs(bbox["width"] - c["width"]) <= tol and abs(bbox["height"] - c["height"]) <= tol):
            return True
    return False


def annotate_screenshot(device: str, slug: str, report: dict, show_all: bool = False):
    """
    Draw bounding boxes onto live sticky screenshots — but only onto the
    screenshot(s) where the flagged element was actually present at that
    scroll position, and skip saving screenshots that end up with nothing
    to show (so we're not saving 10 near-duplicate annotated images).
    """
    live_dir = os.path.join(LIVE_DIR, slug, device)
    if not os.path.isdir(live_dir):
        print(f"  [Annotate] Live directory not found: {live_dir}")
        return

    screenshot_files = [
        f for f in os.listdir(live_dir)
        if f.startswith(f"{device}-{slug}") and f.lower().endswith(('.png', '.jpg'))
    ]
    if not screenshot_files:
        print(f"  [Annotate] No screenshots found for {device}/{slug}.")
        return

    os.makedirs(DIFFS_DIR, exist_ok=True)
    device_diffs_dir = os.path.join(DIFFS_DIR, device, slug)
    os.makedirs(device_diffs_dir, exist_ok=True)

    # --- Non-visual warnings (unchanged from before) ---
    warnings_path = os.path.join(DIFFS_DIR, f"{device}-{slug}-non-visual-warnings.txt")
    with open(warnings_path, "w", encoding="utf-8") as f:
        f.write(f"Non-Visual / SEO Status — Sticky — {device} ({slug})\n")
        f.write("=" * 50 + "\n\n")
        summary = report.get("summary", {})
        f.write("[Sticky Summary]\n")
        f.write(f"- Sticky Elements: {summary.get('sticky', 'N/A')}\n")
        f.write(f"- Canonical:       {summary.get('canonical', 'N/A')}\n")
        f.write(f"- Meta:            {summary.get('meta', 'N/A')}\n")
        f.write(f"- OG Tags:         {summary.get('og_tags', 'N/A')}\n\n")
        f.write("[Non-Visual Mismatches]\n")
        floating = [
            issue.get("message", "")
            for issues in report.get("details", {}).values()
            if isinstance(issues, list)
            for issue in issues
            if issue.get("bbox") is None
        ]
        for msg in floating:
            f.write(f"- {msg}\n")
        if not floating:
            f.write("- All correct!\n")
    print(f"  Non-visual warnings saved.")

    # --- Collect every issue that has a bbox, across all categories ---
    all_issues = [
        issue
        for issues in report.get("details", {}).values()
        if isinstance(issues, list)
        for issue in issues
        if issue.get("bbox") is not None
    ]

    saved_count = 0
    skipped_count = 0

    for screenshot in screenshot_files:
        match = re.search(r"-(\d+)pctscroll-screenshot", screenshot)
        pct = int(match.group(1)) if match else None

        candidates = _bboxes_at_pct(device, slug, pct) if pct is not None else []
        relevant_issues = [
            issue for issue in all_issues
            if _bbox_present(issue["bbox"], candidates)
        ]

        if not relevant_issues:
            skipped_count += 1
            continue  # nothing to show at this scroll point — skip saving a clean duplicate

        img = Image.open(os.path.join(live_dir, screenshot))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=16)
        except Exception:
            font = ImageFont.load_default()

        for issue in relevant_issues:
            bbox = issue["bbox"]
            label = issue.get("message", "Mismatch")
            x, y, w, h = bbox["x"], bbox["y"], bbox["width"], bbox["height"]
            draw.rectangle([(x, y), (x + w, y + h)], outline="red", width=3)
            if len(label) > 60:
                label = label[:57] + "..."
            text_y = max(0, y - 20)
            try:
                text_bbox = draw.textbbox((x, text_y), label, font=font)
                if x + (text_bbox[2] - text_bbox[0]) > img.width:
                    x = max(0, img.width - (text_bbox[2] - text_bbox[0]))
                    text_bbox = draw.textbbox((x, text_y), label, font=font)
                draw.rectangle(text_bbox, fill="red")
            except AttributeError:
                pass
            draw.text((x, text_y), label, fill="white", font=font)

        out_path = os.path.join(device_diffs_dir, screenshot.replace("live-", "annotated-"))
        img.save(out_path)
        saved_count += 1
        print(f"  Annotated screenshot saved to {out_path}")

    print(f"  {saved_count} screenshot(s) annotated, {skipped_count} skipped (no issues at that scroll point).")
        
def compare_device(device: str, slug: str) -> dict:
    print(f"\n[{device}] Comparing sticky UI...")
    ref_soup = load_html("reference", device, slug)
    live_soup = load_html("live", device, slug)
    ref_elements = load_elements("reference", device, slug)
    live_elements = load_elements("live", device, slug)

    results = {
        "headings": compare_headings(ref_soup, live_soup, ref_elements, live_elements),
        "images":   compare_images(ref_soup, live_soup, ref_elements, live_elements),
        "buttons":  compare_buttons(ref_elements, live_elements),
        "links":    compare_links(ref_soup, live_soup, ref_elements, live_elements),
        "canonical": compare_canonical(ref_soup, live_soup),
        "meta":     compare_meta(ref_soup, live_soup),
        "og_tags":  compare_og_tags(ref_soup, live_soup),
        "sticky":   compare_sticky(ref_elements, live_elements),
    }

    for category, (status, issues) in results.items():
        icon = "✅" if status == "PASS" else "❌"
        print(f"  {icon} {category}: {status}" + (f" ({len(issues)} issue(s))" if issues else ""))

    report = {
        "device": device,
        "slug": slug,
        "scope": "sticky",
        "summary": {k: v[0] for k, v in results.items()},
        "details": {k: v[1] for k, v in results.items()},
    }
    return report

def generate_summary_report(all_reports: list, slug: str):
    os.makedirs(DIFFS_DIR, exist_ok=True)
    path = os.path.join(DIFFS_DIR, f"{slug}-sticky-problems.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"STICKY ELEMENT DIFF REPORT — {slug}\n")
        f.write("=" * 60 + "\n\n")
        for report in all_reports:
            device = report["device"]
            details = report.get("details", {})
            
            f.write(f"[ {device.upper()} ]\n")
            f.write("-" * 40 + "\n")
            
            # 1. Sticky Count/Presence Mismatches
            sticky_issues = details.get("sticky", [])
            if sticky_issues:
                f.write("Sticky Layout Issues:\n")
                for issue in sticky_issues:
                    f.write(f"  - {issue.get('message', 'Issue')}\n")
                f.write("\n")
                
            # 2. Buttons (Missing/Extra/Mismatch)
            btn_issues = details.get("buttons", [])
            btn_missing = [i for i in btn_issues if i.get("type") == "missing_button"]
            btn_extra = [i for i in btn_issues if i.get("type") == "extra_button"]
            btn_mismatch = [i for i in btn_issues if i.get("type") == "button_label_mismatch"]
            
            if btn_missing or btn_extra or btn_mismatch:
                f.write("Button Mismatches:\n")
                if btn_missing:
                    f.write(f"  Reference ONLY (missing from live):\n")
                    for i in btn_missing:
                        text = i.get("message", "").replace("Missing button: ", "")
                        f.write(f"    - {text}\n")
                if btn_extra:
                    f.write(f"  Live ONLY (extra buttons):\n")
                    for i in btn_extra:
                        text = i.get("message", "").replace("Extra button in live: ", "")
                        f.write(f"    - {text}\n")
                if btn_mismatch:
                    f.write(f"  Label Mismatches:\n")
                    for i in btn_mismatch:
                        f.write(f"    - {i.get('message')}\n")
                f.write("\n")
                
            # 3. Links (Missing/Extra)
            link_issues = details.get("links", [])
            link_missing = [i for i in link_issues if i.get("type") == "missing_link"]
            link_extra = [i for i in link_issues if i.get("type") == "extra_link"]
            
            if link_missing or link_extra:
                f.write("Link Mismatches:\n")
                if link_missing:
                    f.write(f"  Reference ONLY (missing from live):\n")
                    for i in link_missing:
                        text = i.get("message", "").replace("Missing link: ", "")
                        f.write(f"    - {text}\n")
                if link_extra:
                    f.write(f"  Live ONLY (extra links):\n")
                    for i in link_extra:
                        text = i.get("message", "").replace("Extra link: ", "")
                        f.write(f"    - {text}\n")
                f.write("\n")

            # 4. Images (Missing/Extra/Alt mismatch)
            img_issues = details.get("images", [])
            img_missing = [i for i in img_issues if i.get("type") == "missing_image"]
            img_extra = [i for i in img_issues if i.get("type") == "extra_image"]
            img_alt = [i for i in img_issues if i.get("type") == "alt_mismatch"]
            
            if img_missing or img_extra or img_alt:
                f.write("Image Mismatches:\n")
                if img_missing:
                    f.write(f"  Reference ONLY (missing from live):\n")
                    for i in img_missing:
                        text = i.get("message", "").replace("Image missing in live (alt: ", "").rstrip(")")
                        f.write(f"    - {text}\n")
                if img_extra:
                    f.write(f"  Live ONLY (extra images):\n")
                    for i in img_extra:
                        f.write(f"    - Extra image in live\n")
                if img_alt:
                    f.write(f"  Alt Tag Mismatches:\n")
                    for i in img_alt:
                        f.write(f"    - {i.get('message')}\n")
                f.write("\n")

            # 5. Headings (Missing/Extra/Tag mismatch)
            hd_issues = details.get("headings", [])
            hd_missing = [i for i in hd_issues if i.get("type") == "missing_heading"]
            hd_extra = [i for i in hd_issues if i.get("type") == "extra_heading"]
            hd_mismatch = [i for i in hd_issues if i.get("type") == "heading_tag_mismatch"]
            
            if hd_missing or hd_extra or hd_mismatch:
                f.write("Heading Mismatches:\n")
                if hd_missing:
                    f.write(f"  Reference ONLY (missing from live):\n")
                    for i in hd_missing:
                        f.write(f"    - {i.get('message')}\n")
                if hd_extra:
                    f.write(f"  Live ONLY (extra headings):\n")
                    for i in hd_extra:
                        f.write(f"    - {i.get('message')}\n")
                if hd_mismatch:
                    f.write(f"  Tag Level Mismatches:\n")
                    for i in hd_mismatch:
                        f.write(f"    - {i.get('message')}\n")
                f.write("\n")
                
            if not (sticky_issues or btn_issues or link_issues or img_issues or hd_issues):
                f.write("- No mismatches detected in sticky elements.\n\n")
                
    print(f"Sticky diff report saved to {path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    all_reports = []
    for device in ["desktop", "android", "ios"]:
        try:
            report = compare_device(device, args.slug)
            all_reports.append(report)
            annotate_screenshot(device, args.slug, report, show_all=args.all)
        except FileNotFoundError as e:
            print(f"\n[{device}] Skipping — {e}")

    os.makedirs(REPORTS_DIR, exist_ok=True)
    report_path = os.path.join(REPORTS_DIR, f"sticky-{args.slug}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(all_reports, f, indent=2)
    print(f"\nReport saved to {report_path}")

    generate_summary_report(all_reports, args.slug)
