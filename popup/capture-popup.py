import argparse
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from playwright.sync_api import sync_playwright

# Base directories for popup module
POPUP_DIR = os.path.abspath(os.path.dirname(__file__))
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
def extract_elements(page, fold_height: int, popup_selector: str = None) -> dict:
    elements = {
        "headings": [],
        "images": [],
        "buttons": [],
        "links": [],
        "canonical": [],
        "meta": [],
        "og_tags": [],
    }
    
    root_element = page.query_selector(popup_selector) if popup_selector else None

    # Headings
    for level in range(1, 7):
        tag = f"h{level}"
        items = root_element.query_selector_all(tag) if root_element else page.query_selector_all(tag)
        for el in items:
            bbox = el.bounding_box()
            if bbox and (popup_selector or bbox["y"] < fold_height):
                text = " ".join((el.text_content() or "").split())[:80]
                elements["headings"].append({"tag": tag, "text": text, "bbox": bbox})
    # Images
    items = root_element.query_selector_all("img") if root_element else page.query_selector_all("img")
    for el in items:
        bbox = el.bounding_box()
        if bbox and (popup_selector or bbox["y"] < fold_height):
            elements["images"].append({
                "alt": el.get_attribute("alt") or "",
                "src": el.get_attribute("src") or "",
                "bbox": bbox,
            })
    # Buttons
    button_selectors = ["button", "input[type='button']", "input[type='submit']", "[role='button']"]
    for selector in button_selectors:
        items = root_element.query_selector_all(selector) if root_element else page.query_selector_all(selector)
        for el in items:
            bbox = el.bounding_box()
            if not bbox or (not popup_selector and bbox["y"] > fold_height):
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
    items = root_element.query_selector_all("a") if root_element else page.query_selector_all("a")
    for el in items:
        bbox = el.bounding_box()
        if bbox and (popup_selector or bbox["y"] < fold_height):
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

# Popup selectors — ordered from most specific (ARIA roles) to least (class wildcards)
POPUP_SELECTORS = [
    "div[role='dialog']",
    "div[aria-modal='true']",
    "[role='alertdialog']",
    "div.modal",
    "div.modal-content",
    "div.popup",
    "div.overlay",
    "div.lightbox",
    ".ReactModal__Content",
    "[class*='modal']",
    "[class*='popup']",
    "[class*='overlay']",
    "[class*='dialog']",
    "[id*='modal']",
    "[id*='popup']",
]

def _is_likely_popup(page, box, viewport_w, viewport_h):
    """Filter out tiny widgets, form fields, and hover tooltips.
    A real modal popup typically:
      - Is at least 200×150px and covers ≥5% of the viewport
      - Contains form inputs OR a close button
      - Has a high z-index or fixed/absolute positioning
    Hover tooltips fail these checks.
    """
    if not box:
        return False
    w, h = box["width"], box["height"]
    area_ratio = (w * h) / (viewport_w * viewport_h)
    # Must be a meaningful size — not a small chat widget or input field
    if w < 200 or h < 150:
        return False
    if area_ratio < 0.05:
        return False
    return True

def _has_modal_traits(page, selector):
    """Use JS to verify the element has real modal characteristics,
    not just a hover tooltip that happens to match a popup selector.
    Returns True if the element looks like an actual modal/popup."""
    try:
        return page.evaluate("""(sel) => {
            const el = document.querySelector(sel);
            if (!el) return false;

            // Check 1: Does it contain a close button?
            const hasClose = !!(
                el.querySelector('button[aria-label*="close" i]') ||
                el.querySelector('button[aria-label*="Close"]') ||
                el.querySelector('[class*="close"]') ||
                el.querySelector('button') &&
                Array.from(el.querySelectorAll('button')).some(b =>
                    (b.textContent || '').trim() === '×' ||
                    (b.textContent || '').trim().toLowerCase() === 'close' ||
                    (b.textContent || '').trim() === '✕'
                )
            );

            // Check 2: Does it contain form inputs?
            const hasForm = !!(
                el.querySelector('input[type="text"], input[type="email"], input[type="tel"], input[type="number"], textarea, select') ||
                el.querySelector('form')
            );

            // Check 3: Does it or its parent have a backdrop/overlay?
            const hasBackdrop = !!(
                el.previousElementSibling &&
                (() => {
                    const sib = el.previousElementSibling;
                    const style = window.getComputedStyle(sib);
                    return (style.position === 'fixed' || style.position === 'absolute') &&
                           parseFloat(style.opacity) < 1;
                })()
            ) || !!(
                el.parentElement &&
                (() => {
                    const par = el.parentElement;
                    const style = window.getComputedStyle(par);
                    return (style.position === 'fixed' || style.position === 'absolute') &&
                           style.zIndex && parseInt(style.zIndex) > 100;
                })()
            );

            // Check 4: High z-index (modals are typically z-index > 100)
            const style = window.getComputedStyle(el);
            const zIndex = parseInt(style.zIndex) || 0;
            const highZ = zIndex > 100;

            // A real modal should have at least 2 of these traits
            const score = (hasClose ? 1 : 0) + (hasForm ? 1 : 0) + (hasBackdrop ? 1 : 0) + (highZ ? 1 : 0);
            return score >= 1;
        }""", selector)
    except Exception:
        # If JS check fails, fall back to size-only check
        return True

def _scan_for_popup(page, viewport_w, viewport_h):
    """Scan all POPUP_SELECTORS and return (bbox, selector) of the first likely popup.
    Validates both size AND modal traits to avoid hover tooltips."""
    for selector in POPUP_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0 and locator.is_visible():
                box = locator.bounding_box()
                if _is_likely_popup(page, box, viewport_w, viewport_h):
                    if _has_modal_traits(page, selector):
                        return box, selector
        except Exception:
            continue
    return None, None

def capture_popup(p, browser_kwargs, page_kwargs, url, mode, device, slug):
    fold_height = FIRST_FOLD_HEIGHTS[device]
    viewport = page_kwargs.get("viewport", DESKTOP_VIEWPORT)
    viewport_w = viewport["width"]
    viewport_h = viewport["height"]

    browser = p.chromium.launch(**browser_kwargs)
    page = browser.new_page(**page_kwargs)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("networkidle")

    popup_bbox = None

    # --- Strategy 1: wait up to 10 seconds for popup to auto-appear ---
    print(f"  [Strategy 1] Waiting up to 10s for popup to auto-appear...")
    for attempt in range(10):
        popup_bbox, sel = _scan_for_popup(page, viewport_w, viewport_h)
        if popup_bbox:
            print(f"  ✓ Popup detected via '{sel}' after {attempt}s.")
            break
        page.wait_for_timeout(1000)

    # --- Strategy 2: scroll down to trigger scroll-based popups ---
    if not popup_bbox:
        print(f"  [Strategy 2] Trying scroll trigger...")
        page.evaluate("window.scrollBy(0, 500)")
        page.wait_for_timeout(2000)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(2000)
        popup_bbox, sel = _scan_for_popup(page, viewport_w, viewport_h)
        if popup_bbox:
            print(f"  ✓ Popup detected after scroll via '{sel}'.")

    # --- Strategy 3: simulate tab visibility change (leave & come back) ---
    # This is the key trigger for popups that appear when user
    # switches away from the browser and returns.
    # Runs BEFORE mouse move to avoid false-positive hover tooltips.
    if not popup_bbox:
        print(f"  [Strategy 3] Simulating tab visibility change (leave & return)...")
        page.evaluate("""() => {
            Object.defineProperty(document, 'hidden', {value: true, writable: true, configurable: true});
            Object.defineProperty(document, 'visibilityState', {value: 'hidden', writable: true, configurable: true});
            document.dispatchEvent(new Event('visibilitychange'));
            window.dispatchEvent(new Event('blur'));
            window.dispatchEvent(new Event('pagehide'));
        }""")
        page.wait_for_timeout(2000)
        page.evaluate("""() => {
            Object.defineProperty(document, 'hidden', {value: false, writable: true, configurable: true});
            Object.defineProperty(document, 'visibilityState', {value: 'visible', writable: true, configurable: true});
            document.dispatchEvent(new Event('visibilitychange'));
            window.dispatchEvent(new Event('focus'));
            window.dispatchEvent(new Event('pageshow'));
        }""")
        page.wait_for_timeout(3000)
        popup_bbox, sel = _scan_for_popup(page, viewport_w, viewport_h)
        if popup_bbox:
            print(f"  ✓ Popup detected after visibility change via '{sel}'.")

    # --- Strategy 4: exit-intent (mouse toward top of page / outside viewport) ---
    # Runs after visibility change to avoid hover tooltips triggering first.
    if not popup_bbox:
        print(f"  [Strategy 4] Trying exit-intent mouse move...")
        page.mouse.move(viewport_w // 2, 5)   # near top edge
        page.wait_for_timeout(1500)
        page.mouse.move(viewport_w // 2, 0)   # very top
        page.wait_for_timeout(2000)
        popup_bbox, sel = _scan_for_popup(page, viewport_w, viewport_h)
        if popup_bbox:
            print(f"  ✓ Popup detected after exit-intent via '{sel}'.")

    # --- Strategy 5: longer passive wait (some popups have 15-30s timers) ---
    if not popup_bbox:
        print(f"  [Strategy 5] Longer passive wait (up to 20s more)...")
        for attempt in range(20):
            popup_bbox, sel = _scan_for_popup(page, viewport_w, viewport_h)
            if popup_bbox:
                print(f"  ✓ Popup detected after extended wait ({attempt + 1}s) via '{sel}'.")
                break
            page.wait_for_timeout(1000)

    # ---- Take screenshot ----
    out_dir = get_output_dir(mode, device, slug)
    os.makedirs(out_dir, exist_ok=True)

    if popup_bbox:
        padding = 10
        clip = {
            "x": max(0, popup_bbox["x"] - padding),
            "y": max(0, popup_bbox["y"] - padding),
            "width": popup_bbox["width"] + padding * 2,
            "height": popup_bbox["height"] + padding * 2,
        }
        print(f"  Screenshotting popup ({int(popup_bbox['width'])}×{int(popup_bbox['height'])}px)")
    else:
        print(f"  ⚠ No popup found after all strategies — saving full viewport as fallback.")
        clip = {"x": 0, "y": 0, "width": viewport_w, "height": fold_height}

    page.screenshot(
        path=os.path.join(out_dir, f"{mode}-{device}-{slug}-screenshot.png"),
        full_page=False,
        clip=clip
    )
    print(f"  Screenshot saved.")

    with open(os.path.join(out_dir, f"{mode}-{device}-{slug}-page.html"), "w", encoding="utf-8") as f:
        f.write(page.content())

    elements = extract_elements(page, fold_height, popup_selector=sel if popup_bbox else None)
    elements["screenshot_offset"] = {
        "x": clip["x"],
        "y": clip["y"]
    }
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
    print(f"\nDone. Output saved to {args.mode}/[device]-{args.slug}/")
