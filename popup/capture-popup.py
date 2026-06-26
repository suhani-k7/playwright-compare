import argparse
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from playwright.sync_api import sync_playwright

# Base directories for popup module
POPUP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "popup"))
REFERENCE_DIR = os.path.join(POPUP_DIR, "reference")
LIVE_DIR = os.path.join(POPUP_DIR, "live")
REPORTS_DIR = os.path.join(POPUP_DIR, "reports")
DIFFS_DIR = os.path.join(POPUP_DIR, "diffs")

# Viewport definitions
DESKTOP_VIEWPORT = {"width": 1280, "height": 800}
FIRST_FOLD_HEIGHTS = {"desktop": 800, "android": 851, "ios": 693}

def get_output_dir(mode: str, device: str, slug: str) -> str:
    """Return directory for given mode (reference/live) and device/slug."""
    base = REFERENCE_DIR if mode == "reference" else LIVE_DIR
    return os.path.join(base, f"{device}-{slug}")

# Reuse element extraction from firstfold (same logic) – copy here
def extract_elements(page, fold_height: int) -> dict:
    elements = {
        "headings": [],
        "images": [],
        "buttons": [],
        "links": [],
        "canonical": [],
        "meta": [],
        "og_tags": [],
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
            elements["links"].append({
                "href": el.get_attribute("href") or "",
                "bbox": bbox,
            })
    # Canonical
    canonical_el = page.query_selector("link[rel='canonical']")
    if canonical_el:
        elements["canonical"].append({
            "href": canonical_el.get_attribute("href") or "",
            "bbox": None,
        })
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
            elements["og_tags"].append({
                "property": key,
                "value": el.get_attribute("content") or "",
                "bbox": None,
            })
    return elements

# Simple popup detection – try known selectors, take screenshot of element if found
POPUP_SELECTORS = [
    "div[role='dialog']",
    "div.modal",
    "div[aria-modal='true']",
    "div[role='alertdialog']",
    "button:has-text('Close')",
    "button[aria-label='Close']",
]

def capture_popup(p, browser_kwargs, page_kwargs, url, mode, device, slug):
    fold_height = FIRST_FOLD_HEIGHTS[device]
    browser = p.chromium.launch(**browser_kwargs)
    page = browser.new_page(**page_kwargs)
    page.goto(url, wait_until="load")
    page.wait_for_load_state("networkidle")
    # Detect popup element
    popup_bbox = None
    for selector in POPUP_SELECTORS:
        locator = page.locator(selector).first
        if locator.count() > 0 and locator.is_visible():
            try:
                box = locator.bounding_box()
                if box:
                    popup_bbox = box
                    break
            except Exception:
                pass
    out_dir = get_output_dir(mode, device, slug)
    os.makedirs(out_dir, exist_ok=True)
    # Screenshot
    if popup_bbox:
        clip = {"x": popup_bbox["x"], "y": popup_bbox["y"], "width": popup_bbox["width"], "height": popup_bbox["height"]}
    else:
        # fallback to full viewport
        clip = {"x": 0, "y": 0, "width": page_kwargs.get("viewport", DESKTOP_VIEWPORT)["width"], "height": fold_height}
    page.screenshot(path=os.path.join(out_dir, f"{mode}-{device}-{slug}-screenshot.png"), full_page=False, clip=clip
    print(f"  Popup screenshot saved ({mode}-{device}).")
    # Save HTML
    with open(os.path.join(out_dir, f"{mode}-{device}-{slug}-page.html"), "w", encoding="utf-8") as f:
        f.write(page.content())
    # Save elements JSON (full page elements for now)
    elements = extract_elements(page, fold_height)
    with open(os.path.join(out_dir, f"{mode}-{device}-{slug}-elements.json"), "w", encoding="utf-8") as f:
        json.dump(elements, f, indent=2)
    browser.close()

def capture_url(url: str, mode: str, slug: str):
    with sync_playwright() as p:
        print(f"\n[desktop] Capturing popup: {url}")
        capture_popup(p, {"channel": "chrome", "headless": False}, {"viewport": DESKTOP_VIEWPORT}, url, mode, "desktop", slug)
        print(f"\n[android] Capturing popup: {url}")
        capture_popup(p, {}, p.devices["Pixel 5"], url, mode, "android", slug)
        print(f"\n[ios] Capturing popup: {url}")
        capture_popup(p, {}, p.devices["iPhone 13 Mini"], url, mode, "ios", slug)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--mode", required=True, choices=["reference", "live"])
    parser.add_argument("--slug", required=True)
    args = parser.parse_args()
    capture_url(args.url, args.mode, args.slug)
    print(f"\nDone. Output saved to {args.mode}-popup/[device]-{args.slug}/")
