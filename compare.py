import argparse
import json
import os
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
import urllib.parse

# -------------------------------------------------------------------
# Helpers to load saved capture outputs
# -------------------------------------------------------------------

def _normalize_href(href: str, base_url: str = "", known_domains: list = None) -> str:
    href = href.strip()
    if not href or href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
        return href

    if base_url:
        href = urllib.parse.urljoin(base_url, href)

    parsed = urllib.parse.urlparse(href)
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    query = parsed.query

    # Known internal domains — strip domain, compare path only
    # This prevents prod vs UAT domain differences from creating noise
    internal_domains = known_domains or ["axismaxlife.com", "neouat.axismaxlife.com"]
    is_internal = any(netloc.endswith(d) for d in internal_domains)

    if is_internal or not netloc:
        # Internal or relative — path only
        normalized = path
    else:
        # External link — keep full URL so real external destination changes are caught
        normalized = f"{parsed.scheme.lower()}://{netloc}{path}"

    if query:
        normalized = f"{normalized}?{query}"

    return normalized

def load_html(mode: str, device: str, slug: str) -> BeautifulSoup:
    path = os.path.join(mode, f"{device}-{slug}", f"{mode}-{device}-{slug}-page.html")
    if not os.path.exists(path):
        raise FileNotFoundError(f"HTML not found: {path}. Run capture.py first.")
    with open(path, "r", encoding="utf-8") as f:
        return BeautifulSoup(f.read(), "lxml")


def load_elements(mode: str, device: str, slug: str) -> dict:
    path = os.path.join(mode, f"{device}-{slug}", f"{mode}-{device}-{slug}-elements.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Elements JSON not found: {path}. Run capture.py first.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# -------------------------------------------------------------------
# Individual comparators
# Each returns:
#   status  — "PASS" or "FAIL"
#   details — list of mismatch dicts (used by annotator in Phase 3)
# -------------------------------------------------------------------

def compare_headings(ref_soup, live_soup, ref_elements: dict, live_elements: dict) -> tuple[str, list]:
    """
    Compares H1-H6 across reference and live sequentially.
    Checks: tag mismatch and missing/extra tags.
    """
    mismatches = []

    ref_h = ref_elements.get("headings", [])
    live_h = live_elements.get("headings", [])

    max_len = max(len(ref_h), len(live_h))
    for i in range(max_len):
        r = ref_h[i] if i < len(ref_h) else None
        l = live_h[i] if i < len(live_h) else None

        if r and l:
            if r["tag"] != l["tag"]:
                mismatches.append({
                    "type": "heading_tag_mismatch",
                    "bbox": l["bbox"],
                    "message": f"{l['tag'].upper()}, should have been {r['tag'].upper()}"
                })
        elif r and not l:
            mismatches.append({
                "type": "missing_heading",
                "bbox": r["bbox"],
                "message": f"Missing {r['tag'].upper()}"
            })
        elif not r and l:
            mismatches.append({
                "type": "extra_heading",
                "bbox": l["bbox"],
                "message": f"Extra {l['tag'].upper()}"
            })

    # Global count check
    ref_count = len(ref_h)
    live_count = len(live_h)
    if ref_count != live_count:
        mismatches.append({
            "type": "heading_count",
            "ref_count": ref_count,
            "live_count": live_count,
            "message": f"Heading count: expected {ref_count}, found {live_count}"
        })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches


def compare_images(ref_soup, live_soup, ref_elements: dict, live_elements: dict) -> tuple[str, list]:
    """
    Compares image count and alt attributes by matching src.
    """
    mismatches = []

    ref_imgs = ref_elements.get("images", [])
    live_imgs = live_elements.get("images", [])

    # Count check
    if len(ref_imgs) != len(live_imgs):
        mismatches.append({
            "type": "image_count",
            "ref_count": len(ref_imgs),
            "live_count": len(live_imgs),
            "message": f"Image count: expected {len(ref_imgs)}, found {len(live_imgs)}"
        })

    # Match images by src
    ref_dict = {img["src"]: img for img in ref_imgs if img["src"]}
    live_dict = {img["src"]: img for img in live_imgs if img["src"]}

    for src, l_img in live_dict.items():
        r_img = ref_dict.get(src)
        if r_img:
            if r_img["alt"] != l_img["alt"]:
                mismatches.append({
                    "type": "alt_mismatch",
                    "bbox": l_img["bbox"],
                    "message": f"Alt mismatch. Expected '{r_img['alt']}', found '{l_img['alt']}'"
                })
        else:
            mismatches.append({
                "type": "extra_image",
                "bbox": l_img["bbox"],
                "message": "Extra image in live"
            })

    for src, r_img in ref_dict.items():
        if src not in live_dict:
            mismatches.append({
                "type": "missing_image",
                "bbox": r_img["bbox"],
                "message": f"Image missing in live (alt: '{r_img['alt']}')"
            })

    # Images with no alt at all in live
    for l_img in live_imgs:
        if not l_img["alt"].strip():
            mismatches.append({
                "type": "empty_alt",
                "bbox": l_img["bbox"],
                "message": "Missing alt attribute"
            })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches

def get_btn_compare_key(btn: dict) -> str:
    """Return a stable comparison key for a button.
    Preference order: normalized href → text → aria_label.
    """
    href = _normalize_href(btn.get('href', '').strip())
    if href:
        return href
    text = btn.get('text', '').strip().lower()
    if text:
        return f"text:{text}"
    aria = btn.get('aria_label', '').strip().lower()
    if aria:
        return f"aria:{aria}"
    return ''

def compare_buttons(ref_elements: dict, live_elements: dict) -> tuple[str, list]:
    mismatches = []

    ref_buttons = ref_elements.get("buttons", [])
    live_buttons = live_elements.get("buttons", [])

    # Only count buttons we can actually identify — skip purely icon buttons
    def is_identifiable(btn):
        return bool(
            (btn.get("text") or "").strip() or
            (btn.get("href") or "").strip() or
            (btn.get("aria_label") or "").strip()
        )

    ref_identifiable = [b for b in ref_buttons if is_identifiable(b)]
    live_identifiable = [b for b in live_buttons if is_identifiable(b)]

    # Group by comparison key
    ref_map = {}
    for b in ref_identifiable:
        key = get_btn_compare_key(b)
        ref_map.setdefault(key, []).append(b)

    live_map = {}
    for b in live_identifiable:
        key = get_btn_compare_key(b)
        live_map.setdefault(key, []).append(b)

    ref_map.pop('', None)
    live_map.pop('', None)

    # Count based on identifiable buttons only
    if len(ref_identifiable) != len(live_identifiable):
        mismatches.append({
            "type": "button_count",
            "ref_count": len(ref_identifiable),
            "live_count": len(live_identifiable),
            "message": f"Button count (identifiable): expected {len(ref_identifiable)}, found {len(live_identifiable)}"
        })

    # Missing, extra, label mismatch — rest of logic unchanged
    for key in sorted(ref_map.keys() - live_map.keys()):
        for btn in ref_map[key]:
            mismatches.append({
                "type": "missing_button",
                "bbox": btn.get("bbox"),
                "message": f"Missing button: '{btn.get('text','').strip()}' (href: '{btn.get('href','').strip()}')"
            })

    for key in sorted(live_map.keys() - ref_map.keys()):
        for btn in live_map[key]:
            mismatches.append({
                "type": "extra_button",
                "bbox": btn.get("bbox"),
                "message": f"Extra button in live: '{btn.get('text','').strip()}' (href: '{btn.get('href','').strip()}')"
            })

    for key in sorted(ref_map.keys() & live_map.keys()):
        ref_list = ref_map[key]
        live_list = live_map[key]
        for i in range(max(len(ref_list), len(live_list))):
            if i < len(ref_list) and i < len(live_list):
                ref_btn = ref_list[i]
                live_btn = live_list[i]
                ref_text = ref_btn.get("text", "").strip()
                live_text = live_btn.get("text", "").strip()
                ref_aria = ref_btn.get("aria_label", "").strip()
                live_aria = live_btn.get("aria_label", "").strip()
                if ref_text.lower() != live_text.lower() or ref_aria.lower() != live_aria.lower():
                    mismatches.append({
                        "type": "button_label_mismatch",
                        "bbox": live_btn.get("bbox"),
                        "message": f"Button label mismatch for '{key}': expected '{ref_text}', found '{live_text}'"
                    })
            elif i < len(ref_list):
                ref_btn = ref_list[i]
                mismatches.append({
                    "type": "missing_button",
                    "bbox": ref_btn.get("bbox"),
                    "message": f"Missing button: '{ref_btn.get('text','').strip()}'"
                })
            else:
                live_btn = live_list[i]
                mismatches.append({
                    "type": "extra_button",
                    "bbox": live_btn.get("bbox"),
                    "message": f"Extra button in live: '{live_btn.get('text','').strip()}'"
                })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches
    
def compare_canonical(ref_soup, live_soup) -> tuple[str, list]:
    """
    Checks canonical tag presence and value match.
    """
    mismatches = []

    ref_tag = ref_soup.find("link", rel="canonical")
    live_tag = live_soup.find("link", rel="canonical")

    ref_href = ref_tag["href"].strip() if ref_tag and ref_tag.get("href") else None
    live_href = live_tag["href"].strip() if live_tag and live_tag.get("href") else None

    if ref_href and not live_href:
        mismatches.append({
            "type": "canonical_missing",
            "message": f"Canonical tag missing in live. Expected: {ref_href}"
        })
    elif ref_href and live_href and ref_href != live_href:
        mismatches.append({
            "type": "canonical_mismatch",
            "ref_value": ref_href,
            "live_value": live_href,
            "message": f"Canonical mismatch: ref='{ref_href}' live='{live_href}'"
        })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches


def compare_meta(ref_soup, live_soup) -> tuple[str, list]:
    """
    Compares page title, meta description, and meta keywords.
    """
    mismatches = []

    # Page title
    ref_title = ref_soup.find("title")
    live_title = live_soup.find("title")
    ref_title_text = ref_title.get_text(strip=True) if ref_title else ""
    live_title_text = live_title.get_text(strip=True) if live_title else ""

    if ref_title_text != live_title_text:
        mismatches.append({
            "type": "title_mismatch",
            "ref_value": ref_title_text,
            "live_value": live_title_text,
            "message": "Page title mismatch"
        })

    # Meta description + keywords
    for name in ["description", "keywords"]:
        ref_tag = ref_soup.find("meta", attrs={"name": name})
        live_tag = live_soup.find("meta", attrs={"name": name})
        ref_val = ref_tag.get("content", "").strip() if ref_tag else ""
        live_val = live_tag.get("content", "").strip() if live_tag else ""

        if ref_val != live_val:
            mismatches.append({
                "type": f"meta_{name}_mismatch",
                "ref_value": ref_val,
                "live_value": live_val,
                "message": f"Meta {name} mismatch"
            })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches


def compare_og_tags(ref_soup, live_soup) -> tuple[str, list]:
    """
    Compares og:title, og:description, og:keywords.
    """
    mismatches = []

    for prop in ["og:title", "og:description", "og:keywords"]:
        ref_tag = ref_soup.find("meta", property=prop)
        live_tag = live_soup.find("meta", property=prop)
        ref_val = ref_tag.get("content", "").strip() if ref_tag else ""
        live_val = live_tag.get("content", "").strip() if live_tag else ""

        if ref_val != live_val:
            mismatches.append({
                "type": f"og_tag_mismatch",
                "property": prop,
                "ref_value": ref_val,
                "live_value": live_val,
                "message": f"{prop} mismatch"
            })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches


def compare_links(ref_soup, live_soup, ref_elements: dict, live_elements: dict) -> tuple[str, list]:
    from collections import Counter
    mismatches = []

    ref_links = ref_elements.get("links", [])
    live_links = live_elements.get("links", [])

    # Extract base URLs from canonical tags so relative hrefs resolve correctly
    ref_canonical = ref_soup.find("link", rel="canonical")
    live_canonical = live_soup.find("link", rel="canonical")
    ref_base = ref_canonical["href"].strip() if ref_canonical and ref_canonical.get("href") else ""
    live_base = live_canonical["href"].strip() if live_canonical and live_canonical.get("href") else ""

    # Count check
    if len(ref_links) != len(live_links):
        mismatches.append({
            "type": "link_count",
            "ref_count": len(ref_links),
            "live_count": len(live_links),
            "message": f"Link count: expected {len(ref_links)}, found {len(live_links)}"
        })

    # Build normalized lists, resolving relative URLs against their respective base
    ref_bbox_map = {}
    live_bbox_map = {}

    ref_normalized = []
    for l in ref_links:
        n = _normalize_href(l.get("href", ""), base_url=ref_base)
        ref_normalized.append(n)
        if n not in ref_bbox_map:
            ref_bbox_map[n] = l.get("bbox")

    live_normalized = []
    for l in live_links:
        n = _normalize_href(l.get("href", ""), base_url=live_base)
        live_normalized.append(n)
        if n not in live_bbox_map:
            live_bbox_map[n] = l.get("bbox")

    ref_counter = Counter(ref_normalized)
    live_counter = Counter(live_normalized)

    for href, ref_n in ref_counter.items():
        live_n = live_counter.get(href, 0)
        if live_n < ref_n:
            for _ in range(ref_n - live_n):
                mismatches.append({
                    "type": "missing_link",
                    "bbox": ref_bbox_map.get(href),
                    "message": f"Missing link ({ref_n - live_n}x): {href}"
                })

    for href, live_n in live_counter.items():
        ref_n = ref_counter.get(href, 0)
        if live_n > ref_n:
            for _ in range(live_n - ref_n):
                mismatches.append({
                    "type": "extra_link",
                    "bbox": live_bbox_map.get(href),
                    "message": f"Extra link ({live_n - ref_n}x): {href}"
                })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches
# -------------------------------------------------------------------
# Annotation runner
# -------------------------------------------------------------------

def annotate_screenshot(device: str, slug: str, report: dict):
    """
    Draws bounding boxes and labels on the live screenshot based on the report.
    Saves to the 'diffs/' folder.
    """
    live_img_path = os.path.join("live", f"{device}-{slug}", f"live-{device}-{slug}-screenshot.png")
    if not os.path.exists(live_img_path):
        print(f"  [Annotate] Live screenshot not found: {live_img_path}")
        return

    try:
        img = Image.open(live_img_path)
    except Exception as e:
        print(f"  [Annotate] Failed to open image {live_img_path}: {e}")
        return

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

            x = bbox["x"]
            y = bbox["y"]
            w = bbox["width"]
            h = bbox["height"]
            
            # Draw red rectangle
            draw.rectangle([(x, y), (x + w, y + h)], outline="red", width=3)
            
            # Truncate label if too long
            if len(label) > 60:
                label = label[:57] + "..."
            
            text_y = max(0, y - 20)
            try:
                text_bbox = draw.textbbox((x, text_y), label, font=font)
                label_w = text_bbox[2] - text_bbox[0]
                
                # Shift X if it exceeds image width
                if x + label_w > img.width:
                    x = max(0, img.width - label_w)
                    text_bbox = draw.textbbox((x, text_y), label, font=font)
                
                draw.rectangle(text_bbox, fill="red")
            except AttributeError:
                pass # Fallback for very old Pillow versions that lack textbbox
            
            draw.text((x, text_y), label, fill="white", font=font)

    # Save floating messages and SEO status to a text file
    warnings_path = os.path.join("diffs", f"{device}-{slug}-non-visual-warnings.txt")
    with open(warnings_path, "w", encoding="utf-8") as f:
        f.write(f"Non-Visual / SEO Status for {device} ({slug})\n")
        f.write("="*50 + "\n\n")
        
        # Print SEO Statuses
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
            f.write("- All correct! No non-visual mismatches found.\n")
            
    print(f"  Non-visual warnings saved to {warnings_path}")

    os.makedirs("diffs", exist_ok=True)
    out_path = os.path.join("diffs", f"{device}-{slug}-annotated.png")
    img.save(out_path)
    print(f"  Annotated screenshot saved to {out_path}")

# -------------------------------------------------------------------
# Main comparison runner for one device
# -------------------------------------------------------------------

def compare_device(device: str, slug: str) -> dict:
    """
    Runs all comparators for a single device viewport.
    Returns a result dict with statuses + all mismatch details.
    """
    print(f"\n[{device}] Comparing...")

    ref_soup = load_html("reference", device, slug)
    live_soup = load_html("live", device, slug)
    ref_elements = load_elements("reference", device, slug)
    live_elements = load_elements("live", device, slug)

    # Run all comparators
    heading_status,  heading_issues  = compare_headings(ref_soup, live_soup, ref_elements, live_elements)
    image_status,    image_issues    = compare_images(ref_soup, live_soup, ref_elements, live_elements)
    button_status,   button_issues   = compare_buttons(ref_elements, live_elements)
    canonical_status,canonical_issues= compare_canonical(ref_soup, live_soup)
    meta_status,     meta_issues     = compare_meta(ref_soup, live_soup)
    og_status,       og_issues       = compare_og_tags(ref_soup, live_soup)
    link_status,     link_issues     = compare_links(ref_soup, live_soup, ref_elements, live_elements)

    # Print a quick summary to terminal
    results = {
        "headings":  (heading_status,   heading_issues),
        "images":    (image_status,     image_issues),
        "buttons":   (button_status,    button_issues),
        "canonical": (canonical_status, canonical_issues),
        "meta":      (meta_status,      meta_issues),
        "og_tags":   (og_status,        og_issues),
        "links":     (link_status,      link_issues),
    }

    for category, (status, issues) in results.items():
        icon = "✅" if status == "PASS" else "❌"
        print(f"  {icon} {category}: {status}", end="")
        if issues:
            print(f" ({len(issues)} issue(s))")
        else:
            print()

    # Build the report dict for this device
    report = {
        "device": device,
        "slug": slug,
        "summary": {
            "headings":  heading_status,
            "images":    image_status,
            "buttons":   button_status,
            "canonical": canonical_status,
            "meta":      meta_status,
            "og_tags":   og_status,
            "links":     link_status,
        },
        "details": {
            "headings":  heading_issues,
            "images":    image_issues,
            "buttons":   button_issues,
            "canonical": canonical_issues,
            "meta":      meta_issues,
            "og_tags":   og_issues,
            "links":     link_issues,
        }
    }

    return report

def generate_summary_report(all_reports: list, slug: str):
    os.makedirs("diffs", exist_ok=True)
    path = os.path.join("diffs", f"{slug}-problems.txt")

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"BUTTON DIFF REPORT — {slug}\n")
        f.write("=" * 60 + "\n\n")

        for report in all_reports:
            device = report["device"]
            details = report["details"]
            btn_issues = details.get("buttons", [])

            missing = [i for i in btn_issues if i.get("type") == "missing_button"]
            extra = [i for i in btn_issues if i.get("type") == "extra_button"]

            f.write(f"[ {device.upper()} ]\n")
            f.write("-" * 40 + "\n")

            # Count line
            ref_count = next((i.get("ref_count") for i in btn_issues if i.get("type") == "button_count"), None)
            live_count = next((i.get("live_count") for i in btn_issues if i.get("type") == "button_count"), None)
            if ref_count is not None:
                f.write(f"Reference identifiable buttons: {ref_count}\n")
                f.write(f"Live identifiable buttons:      {live_count}\n\n")

            f.write(f"REF only (missing from live) ({len(missing)}):\n")
            for i in missing:
                text = i.get("message", "").replace("Missing button: ", "").replace("Missing button:", "")
                f.write(f"  - {text}\n")

            f.write(f"\nLIVE only (extra) ({len(extra)}):\n")
            for i in extra:
                text = i.get("message", "").replace("Extra button in live: ", "").replace("Extra button in live:", "")
                f.write(f"  - {text}\n")

            f.write("\n")

    print(f"Button diff report saved to {path}")
# -------------------------------------------------------------------
# CLI entry point
# -------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compare reference vs live HTML structure across all viewports."
    )
    parser.add_argument(
        "--slug",
        required=True,
        help="Page slug used during capture. e.g. rd-calculator"
    )
    args = parser.parse_args()

    all_reports = []
    devices = ["desktop", "android", "ios"]

    for device in devices:
        try:
            report = compare_device(device, args.slug)
            all_reports.append(report)
            annotate_screenshot(device, args.slug, report)
        except FileNotFoundError as e:
            print(f"\n[{device}] Skipping — {e}")

    # Save combined report to reports/
    os.makedirs("reports", exist_ok=True)
    report_path = os.path.join("reports", f"{args.slug}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(all_reports, f, indent=2)

    print(f"\nReport saved to {report_path}")
    generate_summary_report(all_reports, args.slug)