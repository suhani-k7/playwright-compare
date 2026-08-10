import argparse
import json
import os
from playwright.sync_api import sync_playwright
from PIL import Image
import imagehash
from io import BytesIO

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
# -------------------------------------------------------------------
# Device viewport configs
# Playwright has Pixel 5 and iPhone 13 Mini built-in, so we use those
# for accurate UA strings and screen dimensions.
# -------------------------------------------------------------------
DEVICES = ["desktop", "android", "ios"]

DESKTOP_VIEWPORT = {"width": 1280, "height": 800}


def get_output_dir(mode: str, device: str, slug: str) -> str:
    """
    Returns the output directory path for a given mode/device/slug combo.
    e.g. reference/desktop-rd-calculator/
    """
    return os.path.join(DATA_DIR, mode, f"{device}-{slug}")

def _dedupe_buttons(buttons: list) -> list:
    """
    Two DOM elements can both legitimately match our button selectors for
    what is really one clickable control — e.g. <div role="button"><button>
    ...</button></div>. Collapse entries whose bounding boxes nearly fully
    overlap into one, keeping whichever has more identifying info
    (non-empty text/href/aria), tie-breaking toward the smaller (innermost)
    element.
    """
    def overlap_ratio(a, b):
        ax1, ay1 = a["x"], a["y"]
        ax2, ay2 = a["x"] + a["width"], a["y"] + a["height"]
        bx1, by1 = b["x"], b["y"]
        bx2, by2 = b["x"] + b["width"], b["y"] + b["height"]
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        area_a = a["width"] * a["height"] or 1
        area_b = b["width"] * b["height"] or 1
        return inter / min(area_a, area_b)

    def identity_score(btn):
        return (
            (1 if btn.get("text", "").strip() else 0) +
            (1 if btn.get("href", "").strip() else 0) +
            (1 if btn.get("aria_label", "").strip() else 0)
        )

    kept = []
    for btn in buttons:
        merged = False
        for i, existing in enumerate(kept):
            if overlap_ratio(btn["bbox"], existing["bbox"]) > 0.9:
                b_score, e_score = identity_score(btn), identity_score(existing)
                if b_score > e_score:
                    kept[i] = btn
                elif b_score == e_score:
                    b_area = btn["bbox"]["width"] * btn["bbox"]["height"]
                    e_area = existing["bbox"]["width"] * existing["bbox"]["height"]
                    if b_area < e_area:
                        kept[i] = btn
                merged = True
                break
        if not merged:
            kept.append(btn)
    return kept

def extract_elements(page) -> dict:
    """
    Uses Playwright to find all elements we care about and record
    their bounding boxes on the live rendered page.

    Returns a dict of element_type -> list of {bbox, text/attr}
    bbox format: {x, y, width, height}

    Why do this here and not in compare.py?
    Because bounding boxes require a live browser session.
    Once the browser closes, you can't get positions from saved HTML.
    """
    elements = {
        "headings": [],
        "images": [],
        "buttons": [],
        "links": [],
        "canonical": [],
        "meta": [],
        "og_tags": [],
    }

    # --- Headings H1-H6 ---
    for level in range(1, 7):
        tag = f"h{level}"
        heading_els = page.query_selector_all(tag)
        for el in heading_els:
            bbox = el.bounding_box()
            text = el.inner_text().strip()[:80]  # truncate long headings
            if bbox:  # element might be hidden (bbox is None if not visible)
                elements["headings"].append({
                    "tag": tag,
                    "text": text,
                    "bbox": bbox
                })
            
    # --- Images ---
    # phash computation temporarily disabled (image comparator is off for now) —
    # it was adding an individual screenshot + hash per image, which was slow.
    # Re-enable the try/except block below if compare_images is turned back on.
    img_els = page.query_selector_all("img")
    for el in img_els:
        bbox = el.bounding_box()
        alt = el.get_attribute("alt") or ""
        src = el.get_attribute("src") or ""
        phash = None
        if bbox:
            elements["images"].append({
                "alt": alt,
                "src": src,
                "phash": phash,
                "bbox": bbox
            })
    
    # --- Buttons ---
    # Single combined selector: querySelectorAll de-dupes automatically when an
    # element matches more than one part of a comma-separated selector list, so
    # e.g. <button role="button"> is only returned once instead of once per
    # selector we used to loop separately.
    combined_button_selector = "button, input[type='button'], input[type='submit'], [role='button']"
    btn_els = page.query_selector_all(combined_button_selector)
    raw_buttons = []
    for el in btn_els:
        bbox = el.bounding_box()
        text = (el.text_content() or "").strip()[:60]
        href = el.evaluate("""el => {
            let href = el.getAttribute('href');
            if (href) return href;
            let anchorAncestor = el.closest('a');
            if (anchorAncestor) {
                href = anchorAncestor.getAttribute('href');
                if (href) return href;
            }
            let anchorDescendant = el.querySelector('a');
            if (anchorDescendant) {
                href = anchorDescendant.getAttribute('href');
                if (href) return href;
            }
            return '';
        }""") or ""
        aria_label = el.get_attribute("aria-label") or el.get_attribute("aria-labelledby") or ""
        # We no longer know which single selector matched, since we now query all
        # of them together — derive an equivalent descriptive "kind" instead, so
        # compare.py's icon-button bucketing (which reads btn["selector"]) still works.
        kind = el.evaluate("""el => {
            const tag = el.tagName.toLowerCase();
            if (tag === 'input') return "input[type='" + (el.getAttribute('type') || '') + "']";
            if (el.getAttribute('role') === 'button') return "[role='button']";
            return tag;
        }""")
        if bbox:
            raw_buttons.append({
                "selector": kind,
                "text": text,
                "aria_label": aria_label.strip(),
                "href": href.strip(),
                "bbox": bbox
            })

    elements["buttons"] = _dedupe_buttons(raw_buttons)


    # --- Links ---
    link_els = page.query_selector_all("a")
    for el in link_els:
        bbox = el.bounding_box()
        href = el.get_attribute("href") or ""
        if bbox:
            elements["links"].append({
                "href": href,
                "bbox": bbox
            })

    # --- Canonical tag ---
    # This lives in <head> so it has no visual bbox.
    # We still record it here for completeness; compare.py will
    # also read it from the saved HTML.
    canonical_el = page.query_selector("link[rel='canonical']")
    if canonical_el:
        elements["canonical"].append({
            "href": canonical_el.get_attribute("href") or "",
            "bbox": None  # <head> elements have no visual position
        })

    # --- Meta tags ---
    meta_selectors = {
        "title": "title",
        "description": "meta[name='description']",
        "keywords": "meta[name='keywords']",
    }
    for key, selector in meta_selectors.items():
        el = page.query_selector(selector)
        if el:
            # <title> uses inner_text(), meta tags use content attribute
            value = el.inner_text().strip() if key == "title" else (el.get_attribute("content") or "")
            elements["meta"].append({
                "name": key,
                "value": value,
                "bbox": None
            })

    # --- Open Graph tags ---
    og_selectors = {
        "og:title": "meta[property='og:title']",
        "og:description": "meta[property='og:description']",
        "og:keywords": "meta[property='og:keywords']",
    }
    for key, selector in og_selectors.items():
        el = page.query_selector(selector)
        if el:
            value = el.get_attribute("content") or ""
            elements["og_tags"].append({
                "property": key,
                "value": value,
                "bbox": None
            })

    return elements

def _neutralize_sticky_elements(page):
    """
    Full-page screenshots scroll-and-stitch the page together. A sticky/fixed
    nav bar that is actually visible on screen can repaint during that
    stitching and get duplicated in the final image, so we force truly
    visible fixed/sticky elements to static positioning right before the
    screenshot.

    IMPORTANT: we only touch elements that are currently ON SCREEN. Many
    sites hide an off-canvas mobile menu using position:fixed combined with
    an off-screen offset (e.g. left:-100% or a translateX transform) until a
    hamburger click reveals it. Forcing position:static on THAT element would
    remove the off-screen offset's effect and make the hidden menu render
    inline in the document instead — which is worse, not better. So this
    only neutralizes elements whose bounding box actually overlaps the
    current viewport.
    """
    page.evaluate("""
        () => {
            const vw = window.innerWidth;
            const vh = window.innerHeight;
            const all = document.querySelectorAll('*');
            all.forEach(el => {
                const style = window.getComputedStyle(el);
                if (style.position !== 'fixed' && style.position !== 'sticky') return;

                const rect = el.getBoundingClientRect();
                const isOnScreen = (
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    parseFloat(style.opacity) !== 0 &&
                    rect.width > 0 && rect.height > 0 &&
                    rect.right > 0 && rect.bottom > 0 &&
                    rect.left < vw && rect.top < vh
                );

                if (isOnScreen) {
                    el.style.setProperty('position', 'static', 'important');
                }
            });
        }
    """)

def capture_url(url: str, mode: str, slug: str):
    """
    Main capture function. Opens the URL in all 3 viewports,
    saves screenshot + HTML + elements.json for each.
    """
    with sync_playwright() as p:

        # -------------------------------------------------------
        # Viewport 1: Desktop
        # -------------------------------------------------------
        print(f"\n[desktop] Capturing {url}")
        browser = p.chromium.launch(channel="chrome", headless=False)
        page = browser.new_page(viewport=DESKTOP_VIEWPORT)

        page.goto(url, wait_until="load")
        page.wait_for_load_state("networkidle")

        out_dir = get_output_dir(mode, "desktop", slug)
        os.makedirs(out_dir, exist_ok=True)
        
        #_neutralize_sticky_elements(page)
        page.screenshot(path=os.path.join(out_dir, f"{mode}-desktop-{slug}-screenshot.png"), full_page=True)
        print(f"  Screenshot saved.")

        with open(os.path.join(out_dir, f"{mode}-desktop-{slug}-page.html"), "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"  HTML saved.")

        elements = extract_elements(page)
        with open(os.path.join(out_dir, f"{mode}-desktop-{slug}-elements.json"), "w", encoding="utf-8") as f:
            json.dump(elements, f, indent=2)
        print(f"  Elements JSON saved. "
              f"({len(elements['headings'])} headings, "
              f"{len(elements['images'])} images, "
              f"{len(elements['buttons'])} buttons)")

        browser.close()

        # -------------------------------------------------------
        # Viewport 2: Android (Pixel 5)
        # Playwright's device descriptors handle UA + viewport
        # -------------------------------------------------------
        print(f"\n[android] Capturing {url}")
        browser = p.chromium.launch()
        pixel5 = p.devices["Pixel 5"]
        page = browser.new_page(**pixel5)

        page.goto(url, wait_until="load")
        page.wait_for_load_state("networkidle")

        out_dir = get_output_dir(mode, "android", slug)
        os.makedirs(out_dir, exist_ok=True)

        #_sticky_elements(page)
        page.screenshot(path=os.path.join(out_dir, f"{mode}-android-{slug}-screenshot.png"), full_page=True)
        print(f"  Screenshot saved.")

        with open(os.path.join(out_dir, f"{mode}-android-{slug}-page.html"), "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"  HTML saved.")

        elements = extract_elements(page)
        with open(os.path.join(out_dir, f"{mode}-android-{slug}-elements.json"), "w", encoding="utf-8") as f:
            json.dump(elements, f, indent=2)
        print(f"  Elements JSON saved. "
              f"({len(elements['headings'])} headings, "
              f"{len(elements['images'])} images, "
              f"{len(elements['buttons'])} buttons)")

        browser.close()

        # -------------------------------------------------------
        # Viewport 3: iOS (iPhone 13 Mini)
        # -------------------------------------------------------
        print(f"\n[ios] Capturing {url}")
        browser = p.chromium.launch()
        iphone13mini = p.devices["iPhone 13 Mini"]
        page = browser.new_page(**iphone13mini)

        page.goto(url, wait_until="load")
        page.wait_for_load_state("networkidle")

        out_dir = get_output_dir(mode, "ios", slug)
        os.makedirs(out_dir, exist_ok=True)

        #_neutralize_sticky_elements(page)
        page.screenshot(path=os.path.join(out_dir, f"{mode}-ios-{slug}-screenshot.png"), full_page=True)
        print(f"  Screenshot saved.")

        with open(os.path.join(out_dir, f"{mode}-ios-{slug}-page.html"), "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"  HTML saved.")

        elements = extract_elements(page)
        with open(os.path.join(out_dir, f"{mode}-ios-{slug}-elements.json"), "w", encoding="utf-8") as f:
            json.dump(elements, f, indent=2)
        print(f"  Elements JSON saved. "
              f"({len(elements['headings'])} headings, "
              f"{len(elements['images'])} images, "
              f"{len(elements['buttons'])} buttons)")

        browser.close()


# -------------------------------------------------------------------
# CLI entry point
# -------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Capture screenshots, HTML, and element positions for comparison."
    )
    parser.add_argument(
        "--url",
        required=True,
        help="The URL to capture. e.g. https://www.example.com/page"
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["reference", "live"],
        help="Whether this is the reference or live capture."
    )
    parser.add_argument(
        "--slug",
        required=True,
        help="Short identifier for this page. e.g. rd-calculator"
    )

    args = parser.parse_args()
    capture_url(args.url, args.mode, args.slug)
    print(f"\nDone. Output saved to data/{args.mode}/[device]-{args.slug}/")