import argparse
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from playwright.sync_api import sync_playwright

# Base directories for sticky module
STICKY_DIR = os.path.abspath(os.path.dirname(__file__))
REFERENCE_DIR = os.path.join(STICKY_DIR, "reference")
LIVE_DIR = os.path.join(STICKY_DIR, "live")
REPORTS_DIR = os.path.join(STICKY_DIR, "reports")
DIFFS_DIR = os.path.join(STICKY_DIR, "diffs")

# Viewport definitions
DESKTOP_VIEWPORT = {"width": 1280, "height": 800}
FIRST_FOLD_HEIGHTS = {"desktop": 800, "android": 851, "ios": 693}

def get_output_dir(mode: str, device: str, slug: str) -> str:
    """Return directory for given mode (reference/live) and device/slug combination."""
    base = REFERENCE_DIR if mode == "reference" else LIVE_DIR
    return os.path.join(base, f"{device}-{slug}")

def extract_elements(page, fold_height: int) -> dict:
    """Extract the same structural elements as first‑fold capture plus sticky entries."""
    elements = {
        "headings": [],
        "images": [],
        "buttons": [],
        "links": [],
        "canonical": [],
        "meta": [],
        "og_tags": [],
        "sticky": [],
    }
    # Headings
    for level in range(1, 7):
        tag = f"h{level}"
        for el in page.query_selector_all(tag):
            bbox = el.bounding_box()
            if bbox and bbox["y"] < fold_height:
                text = " ".join((el.text_content() or "").split())[:80]
                elements["headings"].append({"tag": tag, "text": text, "bbox": bbox})
    # Images
    for el in page.query_selector_all("img"):
        bbox = el.bounding_box()
        if bbox and bbox["y"] < fold_height:
            elements["images"].append({
                "alt": el.get_attribute("alt") or "",
                "src": el.get_attribute("src") or "",
                "bbox": bbox,
            })
    # Buttons
    button_selectors = ["button", "input[type='button']", "input[type='submit']", "[role='button']"]
    for selector in button_selectors:
        for el in page.query_selector_all(selector):
            bbox = el.bounding_box()
            if not bbox or bbox["y"] > fold_height:
                continue
            text = " ".join((el.text_content() or "").split())[:80]
            href = el.evaluate("""el => {
                let href = el.getAttribute('href');
                if (href) return href;
                let a = el.closest('a');
                if (a) return a.getAttribute('href') || '';
                let d = el.querySelector('a');
                if (d) return d.getAttribute('href') || '';
                return '';
            }""") or ""
            aria_label = el.get_attribute("aria-label") or ""
            elements["buttons"].append({
                "selector": selector,
                "text": text,
                "aria_label": aria_label.strip(),
                "href": href.strip(),
                "bbox": bbox,
            })
    # Links
    for el in page.query_selector_all("a"):
        bbox = el.bounding_box()
        if bbox and bbox["y"] < fold_height:
            elements["links"].append({"href": el.get_attribute("href") or "", "bbox": bbox})
    # Canonical
    canonical_el = page.query_selector("link[rel='canonical']")
    if canonical_el:
        elements["canonical"].append({"href": canonical_el.get_attribute("href") or "", "bbox": None})
    # Meta
    meta_selectors = {"title": "title", "description": "meta[name='description']", "keywords": "meta[name='keywords']"}
    for key, selector in meta_selectors.items():
        el = page.query_selector(selector)
        if el:
            value = (el.text_content() or "").strip() if key == "title" else (el.get_attribute("content") or "")
            elements["meta"].append({"name": key, "value": value, "bbox": None})
    # OG tags
    og_selectors = {"og:title": "meta[property='og:title']", "og:description": "meta[property='og:description']", "og:keywords": "meta[property='og:keywords']"}
    for key, selector in og_selectors.items():
        el = page.query_selector(selector)
        if el:
            elements["og_tags"].append({"property": key, "value": el.get_attribute("content") or "", "bbox": None})
    # Sticky / Fixed elements detection via computed style
    sticky_elements = page.evaluate("""
        () => {
            const elems = Array.from(document.querySelectorAll('*'));
            return elems.filter(el => {
                const style = window.getComputedStyle(el);
                return style.position === 'sticky' || style.position === 'fixed';
            }).map(el => {
                const rect = el.getBoundingClientRect();
                return {
                    tag: el.tagName.toLowerCase(),
                    bbox: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}
                };
            });
        }
    """)
    for entry in sticky_elements:
        elements["sticky"].append({"tag": entry["tag"], "bbox": entry["bbox"]})
    return elements

def capture_sticky(p, browser_kwargs, page_kwargs, url, mode, device, slug):
    fold_height = FIRST_FOLD_HEIGHTS[device]
    browser = p.chromium.launch(**browser_kwargs)
    page = browser.new_page(**page_kwargs)
    page.goto(url, wait_until="load")
    page.wait_for_load_state("networkidle")
    out_dir = get_output_dir(mode, device, slug)
    os.makedirs(out_dir, exist_ok=True)
    # Screenshot – capture full viewport (no clipping) to ensure sticky elements are visible
    page.screenshot(path=os.path.join(out_dir, f"{mode}-{device}-{slug}-screenshot.png"), full_page=False)
    print(f"  Sticky screenshot saved ({mode}-{device}).")
    # Save HTML
    with open(os.path.join(out_dir, f"{mode}-{device}-{slug}-page.html"), "w", encoding="utf-8") as f:
        f.write(page.content())
    # Extract elements (including sticky)
    elements = extract_elements(page, fold_height)
    with open(os.path.join(out_dir, f"{mode}-{device}-{slug}-elements.json"), "w", encoding="utf-8") as f:
        json.dump(elements, f, indent=2)
    browser.close()

def capture_url(url: str, mode: str, slug: str):
    with sync_playwright() as p:
        print(f"\n[desktop] Capturing sticky UI: {url}")
        capture_sticky(p, {"channel": "chrome", "headless": False}, {"viewport": DESKTOP_VIEWPORT}, url, mode, "desktop", slug)
        print(f"\n[android] Capturing sticky UI: {url}")
        capture_sticky(p, {}, p.devices["Pixel 5"], url, mode, "android", slug)
        print(f"\n[ios] Capturing sticky UI: {url}")
        capture_sticky(p, {}, p.devices["iPhone 13 Mini"], url, mode, "ios", slug)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--mode", required=True, choices=["reference", "live"])
    parser.add_argument("--slug", required=True)
    args = parser.parse_args()
    capture_url(args.url, args.mode, args.slug)
    print(f"\nDone. Output saved to {args.mode}/[device]-{args.slug}/")
