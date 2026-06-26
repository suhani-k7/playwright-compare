import argparse
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from playwright.sync_api import sync_playwright

# First fold = one viewport height, no scrolling
DESKTOP_VIEWPORT = {"width": 1280, "height": 800}
FIRST_FOLD_HEIGHTS = {
    "desktop": 800,
    "android": 851,
    "ios":     693,
}

def get_output_dir(mode: str, device: str, slug: str) -> str:
    return os.path.join(
        f"{mode}-firstfold",
        f"{device}-{slug}"
    )

def extract_elements_firstfold(page, fold_height: int) -> dict:
    """
    Same as extract_elements in capture.py, but filters out any element
    whose bounding box starts below the first fold height.
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
        for el in page.query_selector_all(tag):
            bbox = el.bounding_box()
            if bbox and bbox["y"] < fold_height:
                text = " ".join((el.text_content() or "").split())[:80]
                elements["headings"].append({"tag": tag, "text": text, "bbox": bbox})

    # --- Images ---
    for el in page.query_selector_all("img"):
        bbox = el.bounding_box()
        if bbox and bbox["y"] < fold_height:
            elements["images"].append({
                "alt": el.get_attribute("alt") or "",
                "src": el.get_attribute("src") or "",
                "bbox": bbox
            })

    # --- Buttons ---
    button_selectors = [
        "button",
        "input[type='button']",
        "input[type='submit']",
        "[role='button']"
    ]
    for selector in button_selectors:
        for el in page.query_selector_all(selector):
            bbox = el.bounding_box()
            if not bbox or bbox["y"] >= fold_height:
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
                "bbox": bbox
            })

    # --- Links ---
    for el in page.query_selector_all("a"):
        bbox = el.bounding_box()
        if bbox and bbox["y"] < fold_height:
            elements["links"].append({
                "href": el.get_attribute("href") or "",
                "bbox": bbox
            })

    # --- Head elements (no bbox filter needed) ---
    canonical_el = page.query_selector("link[rel='canonical']")
    if canonical_el:
        elements["canonical"].append({
            "href": canonical_el.get_attribute("href") or "",
            "bbox": None
        })

    meta_selectors = {
        "title": "title",
        "description": "meta[name='description']",
        "keywords": "meta[name='keywords']",
    }
    for key, selector in meta_selectors.items():
        el = page.query_selector(selector)
        if el:
            value = (el.text_content() or "").strip() if key == "title" else (el.get_attribute("content") or "")
            elements["meta"].append({"name": key, "value": value, "bbox": None})

    og_selectors = {
        "og:title": "meta[property='og:title']",
        "og:description": "meta[property='og:description']",
        "og:keywords": "meta[property='og:keywords']",
    }
    for key, selector in og_selectors.items():
        el = page.query_selector(selector)
        if el:
            elements["og_tags"].append({
                "property": key,
                "value": el.get_attribute("content") or "",
                "bbox": None
            })

    return elements

def dismiss_popups(page):
    """
    Dismiss known popups before capturing screenshots.
    Safe to call even if no popup exists.
    """

    popup_selectors = [
        "div[aria-label='Close popup']",
        "[aria-label='Close popup']",
        ".new-investment-popup-close",
        "button:has-text('Accept')",
        "button:has-text('Accept All')",
        "button:has-text('Allow')",
        "button[aria-label='Close']",
    ]

    for selector in popup_selectors:
        try:
            locator = page.locator(selector).first

            if locator.count() > 0 and locator.is_visible():
                print(f"Trying popup selector: {selector}")
                locator.click(force=True)
                page.wait_for_timeout(1000)
                print(f"Dismissed popup using: {selector}")
                break

        except Exception:
            pass
            
def capture_viewport(p, browser_kwargs, page_kwargs, url, mode, device, slug):
    fold_height = FIRST_FOLD_HEIGHTS[device]
    browser = p.chromium.launch(**browser_kwargs)
    page = browser.new_page(**page_kwargs)
    page.goto(url, wait_until="load")
    page.wait_for_load_state("networkidle")
    dismiss_popups(page)

    out_dir = get_output_dir(mode, device, slug)
    os.makedirs(out_dir, exist_ok=True)

    # Screenshot only the first fold — clip to viewport height
    page.screenshot(
        path=os.path.join(out_dir, f"{mode}-{device}-{slug}-screenshot.png"),
        full_page=False,  # viewport only, no scrolling
        clip={"x": 0, "y": 0, "width": page_kwargs.get("viewport", DESKTOP_VIEWPORT)["width"], "height": fold_height}
        if "viewport" in page_kwargs
        else {"x": 0, "y": 0, "width": 1280, "height": fold_height}
    )
    print(f"  First-fold screenshot saved ({fold_height}px).")

    with open(os.path.join(out_dir, f"{mode}-{device}-{slug}-page.html"), "w", encoding="utf-8") as f:
        f.write(page.content())
    print(f"  HTML saved.")

    elements = extract_elements_firstfold(page, fold_height)
    with open(os.path.join(out_dir, f"{mode}-{device}-{slug}-elements.json"), "w", encoding="utf-8") as f:
        json.dump(elements, f, indent=2)
    print(f"  Elements JSON saved. ({len(elements['headings'])} headings, {len(elements['images'])} images, {len(elements['buttons'])} buttons)")

    browser.close()


def capture_url(url: str, mode: str, slug: str):
    with sync_playwright() as p:
        print(f"\n[desktop] Capturing first fold: {url}")
        capture_viewport(p,
            browser_kwargs={"channel": "chrome", "headless": False},
            page_kwargs={"viewport": DESKTOP_VIEWPORT},
            url=url, mode=mode, device="desktop", slug=slug)

        print(f"\n[android] Capturing first fold: {url}")
        capture_viewport(p,
            browser_kwargs={},
            page_kwargs=p.devices["Pixel 5"],
            url=url, mode=mode, device="android", slug=slug)

        print(f"\n[ios] Capturing first fold: {url}")
        capture_viewport(p,
            browser_kwargs={},
            page_kwargs=p.devices["iPhone 13 Mini"],
            url=url, mode=mode, device="ios", slug=slug)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--mode", required=True, choices=["reference", "live"])
    parser.add_argument("--slug", required=True)
    args = parser.parse_args()
    capture_url(args.url, args.mode, args.slug)
    print(f"\nDone. Output saved to {args.mode}-firstfold/[device]-{args.slug}/")