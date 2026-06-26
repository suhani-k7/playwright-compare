import argparse
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)

# Import all comparators from parent compare.py
from compare import (
    compare_headings,
    compare_images,
    compare_buttons,
    compare_canonical,
    compare_meta,
    compare_og_tags,
    compare_links,
    _normalize_href,
)


def load_html(mode: str, device: str, slug: str) -> BeautifulSoup:
    path = os.path.join(PROJECT_ROOT, f"{mode}-firstfold", f"{device}-{slug}", f"{mode}-{device}-{slug}-page.html")
    if not os.path.exists(path):
        raise FileNotFoundError(f"HTML not found: {path}. Run capture-firstfold.py first.")
    with open(path, "r", encoding="utf-8") as f:
        return BeautifulSoup(f.read(), "lxml")


def load_elements(mode: str, device: str, slug: str) -> dict:
    path = os.path.join(PROJECT_ROOT, f"{mode}-firstfold", f"{device}-{slug}", f"{mode}-{device}-{slug}-elements.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Elements JSON not found: {path}. Run capture-firstfold.py first.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def annotate_screenshot(device: str, slug: str, report: dict):
    live_img_path = os.path.join(PROJECT_ROOT, "live-firstfold", f"{device}-{slug}", f"live-{device}-{slug}-screenshot.png")
    if not os.path.exists(live_img_path):
        print(f"  [Annotate] Screenshot not found: {live_img_path}")
        return

    img = Image.open(live_img_path)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=16)
    except Exception:
        font = ImageFont.load_default()

    details = report.get("details", {})
    floating_messages = []

    for category, issues in details.items():
        if not isinstance(issues, list):
            continue
        for issue in issues:
            bbox = issue.get("bbox")
            label = issue.get("message", "Mismatch")
            if bbox is None:
                floating_messages.append(label)
                continue
            x, y, w, h = bbox["x"], bbox["y"], bbox["width"], bbox["height"]
            draw.rectangle([(x, y), (x + w, y + h)], outline="red", width=3)
            if len(label) > 60:
                label = label[:57] + "..."
            text_y = max(0, y - 20)
            try:
                text_bbox = draw.textbbox((x, text_y), label, font=font)
                label_w = text_bbox[2] - text_bbox[0]
                if x + label_w > img.width:
                    x = max(0, img.width - label_w)
                    text_bbox = draw.textbbox((x, text_y), label, font=font)
                draw.rectangle(text_bbox, fill="red")
            except AttributeError:
                pass
            draw.text((x, text_y), label, fill="white", font=font)

    os.makedirs(os.path.join(PROJECT_ROOT, "diffs-firstfold"), exist_ok=True)

    # Non-visual warnings
    warnings_path = os.path.join(PROJECT_ROOT, "diffs-firstfold", f"{device}-{slug}-non-visual-warnings.txt")
    with open(warnings_path, "w", encoding="utf-8") as f:
        f.write(f"Non-Visual / SEO Status — First Fold — {device} ({slug})\n")
        f.write("=" * 50 + "\n\n")
        summary = report.get("summary", {})
        f.write("[SEO Status Overview]\n")
        f.write(f"- Canonical Tags: {summary.get('canonical', 'N/A')}\n")
        f.write(f"- Meta Tags:      {summary.get('meta', 'N/A')}\n")
        f.write(f"- Open Graph:     {summary.get('og_tags', 'N/A')}\n\n")
        f.write("[Specific Non-Visual Mismatches]\n")
        if floating_messages:
            for msg in floating_messages:
                f.write(f"- {msg}\n")
        else:
            f.write("- All correct!\n")
    print(f"  Non-visual warnings saved.")

    out_path = os.path.join(PROJECT_ROOT, "diffs-firstfold", f"{device}-{slug}-annotated.png")
    img.save(out_path)
    print(f"  Annotated screenshot saved to {out_path}")


def compare_device(device: str, slug: str) -> dict:
    print(f"\n[{device}] Comparing first fold...")

    ref_soup = load_html("reference", device, slug)
    live_soup = load_html("live", device, slug)
    ref_elements = load_elements("reference", device, slug)
    live_elements = load_elements("live", device, slug)

    results = {
        "headings":  compare_headings(ref_soup, live_soup, ref_elements, live_elements),
        "images":    compare_images(ref_soup, live_soup, ref_elements, live_elements),
        "buttons":   compare_buttons(ref_elements, live_elements),
        "canonical": compare_canonical(ref_soup, live_soup),
        "meta":      compare_meta(ref_soup, live_soup),
        "og_tags":   compare_og_tags(ref_soup, live_soup),
        "links":     compare_links(ref_soup, live_soup, ref_elements, live_elements),
    }

    for category, (status, issues) in results.items():
        icon = "✅" if status == "PASS" else "❌"
        print(f"  {icon} {category}: {status}" + (f" ({len(issues)} issue(s))" if issues else ""))

    report = {
        "device": device,
        "slug": slug,
        "scope": "first_fold",
        "summary": {k: v[0] for k, v in results.items()},
        "details": {k: v[1] for k, v in results.items()},
    }
    return report


def generate_summary_report(all_reports: list, slug: str):
    os.makedirs(os.path.join(PROJECT_ROOT, "diffs-firstfold"), exist_ok=True)
    path = os.path.join(PROJECT_ROOT, "diffs-firstfold", f"{slug}-problems.txt")

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"FIRST FOLD BUTTON DIFF REPORT — {slug}\n")
        f.write("=" * 60 + "\n\n")

        for report in all_reports:
            device = report["device"]
            details = report["details"]
            btn_issues = details.get("buttons", [])
            missing = [i for i in btn_issues if i.get("type") == "missing_button"]
            extra = [i for i in btn_issues if i.get("type") == "extra_button"]

            f.write(f"[ {device.upper()} ]\n")
            f.write("-" * 40 + "\n")

            ref_count = next((i.get("ref_count") for i in btn_issues if i.get("type") == "button_count"), None)
            live_count = next((i.get("live_count") for i in btn_issues if i.get("type") == "button_count"), None)
            if ref_count is not None:
                f.write(f"Reference identifiable buttons: {ref_count}\n")
                f.write(f"Live identifiable buttons:      {live_count}\n\n")

            f.write(f"REF only (missing from live) ({len(missing)}):\n")
            for i in missing:
                text = i.get("message", "").replace("Missing button: ", "")
                f.write(f"  - {text}\n")

            f.write(f"\nLIVE only (extra) ({len(extra)}):\n")
            for i in extra:
                text = i.get("message", "").replace("Extra button in live: ", "")
                f.write(f"  - {text}\n")

            f.write("\n")

    print(f"Button diff report saved to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    args = parser.parse_args()

    all_reports = []
    for device in ["desktop", "android", "ios"]:
        try:
            report = compare_device(device, args.slug)
            all_reports.append(report)
            annotate_screenshot(device, args.slug, report)
        except FileNotFoundError as e:
            print(f"\n[{device}] Skipping — {e}")

    os.makedirs(os.path.join(PROJECT_ROOT, "reports-firstfold"), exist_ok=True)
    report_path = os.path.join(PROJECT_ROOT, "reports-firstfold", f"firstfold-{args.slug}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(all_reports, f, indent=2)
    print(f"\nReport saved to {report_path}")

    generate_summary_report(all_reports, args.slug)