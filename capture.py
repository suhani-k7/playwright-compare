import argparse
import json
import os
from playwright.sync_api import sync_playwright

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
    return os.path.join(mode, f"{device}-{slug}")


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
    img_els = page.query_selector_all("img")
    for el in img_els:
        bbox = el.bounding_box()
        alt = el.get_attribute("alt") or ""
        src = el.get_attribute("src") or ""
        if bbox:
            elements["images"].append({
                "alt": alt,
                "src": src,
                "bbox": bbox
            })

    # --- Buttons ---
    # The docs specify 4 types of buttons
    button_selectors = [
        "button",
        "input[type='button']",
        "input[type='submit']",
        "[role='button']"
    ]
    for selector in button_selectors:
        btn_els = page.query_selector_all(selector)
        for el in btn_els:
            bbox = el.bounding_box()
            text = (el.text_content() or "").strip()[:60]
            if bbox:
                elements["buttons"].append({
                    "selector": selector,
                    "text": text,
                    "bbox": bbox
                })

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

        page.screenshot(path=os.path.join(out_dir, "screenshot.png"), full_page=True)
        print(f"  Screenshot saved.")

        with open(os.path.join(out_dir, "page.html"), "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"  HTML saved.")

        elements = extract_elements(page)
        with open(os.path.join(out_dir, "elements.json"), "w", encoding="utf-8") as f:
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

        page.screenshot(path=os.path.join(out_dir, "screenshot.png"), full_page=True)
        print(f"  Screenshot saved.")

        with open(os.path.join(out_dir, "page.html"), "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"  HTML saved.")

        elements = extract_elements(page)
        with open(os.path.join(out_dir, "elements.json"), "w", encoding="utf-8") as f:
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

        page.screenshot(path=os.path.join(out_dir, "screenshot.png"), full_page=True)
        print(f"  Screenshot saved.")

        with open(os.path.join(out_dir, "page.html"), "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"  HTML saved.")

        elements = extract_elements(page)
        with open(os.path.join(out_dir, "elements.json"), "w", encoding="utf-8") as f:
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
    print(f"\nDone. Output saved to {args.mode}/[device]-{args.slug}/")