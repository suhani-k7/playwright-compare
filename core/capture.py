import argparse
import json
import os
from playwright.sync_api import sync_playwright
from PIL import Image
import imagehash
from io import BytesIO
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError



BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    e.g. reference/rd-calculator/desktop/
    """
    return os.path.join(DATA_DIR, mode, slug, device)

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

def extract_elements(page, device_scale_factor: float = 1) -> dict:
    """
    Uses Playwright to find all elements we care about and record
    their bounding boxes on the live rendered page.

    Returns a dict of element_type -> list of {bbox, text/attr}
    bbox format: {x, y, width, height}

    Why do this here and not in compare.py?
    Because bounding boxes require a live browser session.
    Once the browser closes, you can't get positions from saved HTML.
    """
    def scale_bbox(bbox):
        if not bbox:
            return None
        return {
            "x": bbox["x"] * device_scale_factor,
            "y": bbox["y"] * device_scale_factor,
            "width": bbox["width"] * device_scale_factor,
            "height": bbox["height"] * device_scale_factor,
        }

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
                    "bbox": scale_bbox(bbox)
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
                "bbox": scale_bbox(bbox)
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
                "bbox": scale_bbox(bbox)
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
                "bbox": scale_bbox(bbox)
            })
        else:
            # The element exists in the DOM but has no rendered size — e.g. it is
            # inside a collapsed accordion/dropdown on mobile. Record it anyway
            # (without a bbox) so the comparator can still flag it as missing.
            elements["links"].append({
                "href": href,
                "bbox": None,
                "hidden": True
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

def _scroll_full_page(page, max_scroll_height: int = 30000, max_iterations: int = 150):
    """
    Step-scrolls to the bottom of the page to trigger lazy-loaded images,
    with a hard cap on both total scroll distance and iteration count.
    Without a cap, pages with infinite-scroll or continuously-appending
    content (scrollHeight keeps growing as you approach it) can produce
    an extremely tall page that takes minutes to screenshot or exceeds
    the timeout entirely.
    """
    page.evaluate(f"""
        async () => {{
            await new Promise((resolve) => {{
                let totalHeight = 0;
                let iterations = 0;
                const distance = 400;
                const maxHeight = {max_scroll_height};
                const maxIterations = {max_iterations};
                const timer = setInterval(() => {{
                    window.scrollBy(0, distance);
                    totalHeight += distance;
                    iterations += 1;
                    const reachedBottom = totalHeight >= document.body.scrollHeight;
                    const hitCap = totalHeight >= maxHeight || iterations >= maxIterations;
                    if (reachedBottom || hitCap) {{
                        clearInterval(timer);
                        resolve();
                    }}
                }}, 100);
            }});
        }}
    """)
    page.wait_for_timeout(500)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)

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
def _dismiss_popup(page, max_wait_ms: int = 3000):
    close_selectors = [
        ".new-investment-popup-close",
        "[aria-label='Close popup']",
        "[aria-label*='close' i]",
        "[class*='popup'] [class*='close']",   # only match close buttons INSIDE a popup/modal container
        "[class*='modal'] [class*='close']",
        ".close",
        ".close-btn",
        ".close-button",
        "[class*='close' i]:not([class*='closetab'])",
        "[id*='close' i]",
        "text=×",
    ]
    # Quick DOM check: if no popup or modal container classes exist, limit the maximum wait time to 1s
    has_popup_el = page.evaluate("""() => {
        return !!document.querySelector(
            '[class*="popup"], [class*="modal"], [class*="dialog"], [class*="smartech"], .new-investment-popup-close, [class*="close" i]:not([class*="closetab"]), [id*="close" i]'
        );
    }""")
    
    actual_max_wait = max_wait_ms if has_popup_el else 1000
    waited = 0
    step = 250
    while waited < actual_max_wait:
        for selector in close_selectors:
            try:
                btn = page.locator(selector).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(timeout=1000)
                    page.wait_for_timeout(500)
                    print("  Popup detected and dismissed.")
                    return
            except Exception:
                continue
        page.wait_for_timeout(step)
        waited += step



def _goto_with_retry(page, url: str, retries: int = 1, timeout: int = 45000):
    """
    Navigates to the URL. Uses domcontentloaded instead of load, with a
    longer timeout and one retry — covers both slow-load timeouts and
    transient network errors (e.g. ERR_NETWORK_CHANGED from a Wi-Fi blip).
    """
    last_err = None
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return
        except (PlaywrightTimeoutError, PlaywrightError) as e:
            last_err = e
            print(f"  goto failed ({type(e).__name__}), attempt {attempt + 1}/{retries + 1}, retrying...")
    raise last_err

def _wait_page_ready(page, selector: str = "body", timeout: int = 15000):
    """
    Waits for the page to be usably rendered without relying on
    networkidle, which can hang forever on pages with persistent
    background network activity.
    """
    try:
        page.wait_for_selector(selector, timeout=timeout)
    except PlaywrightTimeoutError:
        print(f"  Selector '{selector}' not found within {timeout}ms, continuing anyway")

def _wait_images_loaded(page, timeout: int = 3000):
    """
    Polls until every <img> on the page reports complete (loaded or
    errored) or the timeout elapses. _scroll_full_page only waits a
    fixed 500ms after triggering lazy-loaded images, which isn't
    always enough on image-heavy pages — this closes that gap right
    before the screenshot is taken.
    """
    try:
        page.wait_for_function(
            """() => Array.from(document.images).every(img => img.complete)""",
            timeout=timeout
        )
    except PlaywrightTimeoutError:
        print("  Not all images finished loading within timeout, continuing anyway")

def _screenshot_full_page_stable(page, path: str, timeout: int = 60000):
    """
    Takes a full-page screenshot. If the page physical height (CSS height * device scale factor)
    exceeds Chromium's texture size rendering limit (typically 16,384 physical px),
    it takes multiple screenshots in safe chunks (max 14,000 physical px) and stitches them
    vertically using Pillow. This avoids the tiled repetition bugs of Chromium's compositor
    under mobile emulation. Otherwise, it takes a fast single-pass screenshot.

    For chunked capture, fixed/sticky elements (e.g. the sticky nav header) are temporarily
    hidden with visibility:hidden before capture begins to prevent them from painting over
    page content in every chunk. Their visibility is restored after stitching.
    NOTE: We use visibility:hidden (not display:none or position:static) to preserve page
    layout while making elements invisible — avoiding the blue-overlay artifact that occurred
    when position:static was used and the nav flowed into the content area.
    """
    total_height = page.evaluate("document.body.scrollHeight")
    dsf = page.evaluate("window.devicePixelRatio") or 1.0
    current_viewport = page.viewport_size
    width = current_viewport["width"] if current_viewport else 1280
    height = current_viewport["height"] if current_viewport else 800

    # Safe physical rendering limit (well under 16,384px)
    max_physical_height = 14000

    # We only capture in a single pass if the page is short (<= 2 * viewport height)
    # AND fits within the physical limit. This prevents layout reflow bugs (like
    # stretched containers and footer gaps) that occur when viewport height is
    # resized to very large values.
    is_short_page = total_height <= 2 * height
    fits_limit = total_height * dsf <= max_physical_height

    if is_short_page and fits_limit:
        page.set_viewport_size({"width": width, "height": total_height})
        page.wait_for_timeout(300)
        new_total_height = page.evaluate("document.body.scrollHeight")
        if new_total_height != total_height and new_total_height * dsf <= max_physical_height:
            page.set_viewport_size({"width": width, "height": new_total_height})
            page.wait_for_timeout(200)
        page.screenshot(path=path, full_page=False, timeout=timeout)
        if current_viewport:
            page.set_viewport_size(current_viewport)
        return

    # --- Chunked path ---
    # We scroll and capture in chunks of the original viewport height (no resizing)
    # to maintain the identical layout as real-world screen sizes.
    chunk_height_css = height
    print(f"  Page height ({total_height}px) is tall. Capturing in chunks of original viewport height ({chunk_height_css}px).")

    # JS snippets used inside the chunk loop.
    #
    # We inject a <style> tag instead of setting inline visibility, because
    # React re-renders on scroll events can overwrite inline styles — but they
    # cannot remove a <style> tag injected into <head>. Using !important in the
    # stylesheet rule ensures the rule wins over any framework-applied styles.
    _ENSURE_STYLE_JS = """
        () => {
            if (!document.getElementById('_capture_hide_nav_style')) {
                const style = document.createElement('style');
                style.id = '_capture_hide_nav_style';
                style.textContent = '[data-_capturehide="1"] { visibility: hidden !important; }';
                document.head.appendChild(style);
            }
        }
    """


    _HIDE_BOTTOM_ONLY_JS = """
        () => {
            const vw = window.innerWidth;
            const vh = window.innerHeight;
            document.querySelectorAll('*').forEach(el => {
                const s = window.getComputedStyle(el);
                if (s.position !== 'fixed' && s.position !== 'sticky') return;
                const rect = el.getBoundingClientRect();
                // Bottom-pinned sticky banners / CTA bars only.
                if (rect.bottom > vh - 120 && rect.height > 0 && rect.height < 200 && rect.width > vw * 0.5) {
                    el.setAttribute('data-_capturehide', '1');
                }
            });
        }
    """
    _HIDE_FIXED_ELEMENTS_JS = """
        () => {
            const vw = window.innerWidth;
            const vh = window.innerHeight;
            document.querySelectorAll('*').forEach(el => {
                const s = window.getComputedStyle(el);
                if (s.position !== 'fixed' && s.position !== 'sticky') return;
                const rect = el.getBoundingClientRect();
                // Top-pinned nav bars.
                const isTopBar = (
                    rect.top < 120 &&
                    rect.height > 0 && rect.height < 200 &&
                    rect.width > vw * 0.5
                );
                // Bottom-pinned sticky banners / CTA bars.
                const isBottomBar = (
                    rect.bottom > vh - 120 &&
                    rect.height > 0 && rect.height < 200 &&
                    rect.width > vw * 0.5
                );
                if (isTopBar || isBottomBar) {
                    el.setAttribute('data-_capturehide', '1');
                }
            });
        }
    """
    _RESTORE_FIXED_ELEMENTS_JS = """
        () => {
            const style = document.getElementById('_capture_hide_nav_style');
            if (style) style.remove();
            document.querySelectorAll('[data-_capturehide]').forEach(el => {
                el.removeAttribute('data-_capturehide');
            });
        }
    """
    _UNLOCK_SCROLL_JS = """
        () => {
            // Popups often set overflow:hidden on body/html to prevent scroll.
            // Remove it so window.scrollTo() works correctly between chunks.
            document.body.style.removeProperty('overflow');
            document.documentElement.style.removeProperty('overflow');
        }
    """

    chunks = []
    y_offset = 0
    any_hidden = False

    # Ensure viewport size matches the original viewport size
    if current_viewport:
        page.set_viewport_size(current_viewport)

    # Inject the hide stylesheet once upfront so it persists across re-renders.
    page.evaluate(_ENSURE_STYLE_JS)

    while y_offset < total_height:
        remaining = total_height - y_offset
        is_last = remaining < chunk_height_css

        # Calculate target scroll position for this step
        if not is_last:
            scroll_pos = y_offset
        else:
            max_scroll = page.evaluate("document.body.scrollHeight - window.innerHeight")
            scroll_pos = min(total_height - chunk_height_css, max_scroll)
            scroll_pos = max(0, scroll_pos)

        # 1. Scroll first and wait for the layout to settle (triggering scroll events)
        page.evaluate(f"window.scrollTo(0, {scroll_pos})")
        page.wait_for_timeout(300)

        # 2. Unlock body overflow and dismiss any popup/modal that was triggered by the scroll
        page.evaluate(_UNLOCK_SCROLL_JS)
        _dismiss_popup(page, max_wait_ms=500)

        # 3. Apply the CSS hiding stylesheet rule (React re-renders on scroll have already finished)
        if scroll_pos == 0:
            # First chunk: show top navigation, hide sticky bottom banner
            page.evaluate(_HIDE_BOTTOM_ONLY_JS)
        else:
            # Subsequent chunks: hide all fixed/sticky elements (top and bottom)
            page.evaluate(_HIDE_FIXED_ELEMENTS_JS)
        any_hidden = True

        # 4. Take the screenshot immediately after hiding
        chunk_data = page.screenshot(full_page=False, timeout=timeout)

        if not is_last:
            chunks.append((Image.open(BytesIO(chunk_data)), chunk_height_css))
            y_offset += chunk_height_css
        else:
            img = Image.open(BytesIO(chunk_data))
            # Crop the bottom 'remaining' CSS pixels transformed to physical pixels
            physical_remaining = int(remaining * dsf)
            crop_top = img.height - physical_remaining
            cropped_img = img.crop((0, crop_top, img.width, img.height))
            chunks.append((cropped_img, remaining))
            break

    # Restore all fixed elements to their original state
    if any_hidden:
        page.evaluate(_RESTORE_FIXED_ELEMENTS_JS)


    # Stitch the chunks vertically
    total_physical_width = chunks[0][0].width
    total_physical_height = sum(c[0].height for c in chunks)
    print(f"  Stitching {len(chunks)} chunks into a {total_physical_width}x{total_physical_height} image...")

    stitched_image = Image.new("RGBA", (total_physical_width, total_physical_height))
    current_y = 0
    for img, _ in chunks:
        stitched_image.paste(img, (0, current_y))
        current_y += img.height

    stitched_image.save(path)

    # Restore original viewport state and scroll to top
    if current_viewport:
        page.set_viewport_size(current_viewport)
    page.evaluate("window.scrollTo(0, 0)")


def capture_url(url: str, mode: str, slug: str):
    """
    Main capture function. Opens the URL in all 3 viewports,
    saves screenshot + HTML + elements.json for each.
    """
    with sync_playwright() as p:
        is_headless = (mode != "live")
        # -------------------------------------------------------
        # Viewport 1: Desktop
        # -------------------------------------------------------
        print(f"\n[desktop] Capturing {url}")
        browser = p.chromium.launch(headless=is_headless)
        page = browser.new_page(viewport=DESKTOP_VIEWPORT)
        _goto_with_retry(page, url)
        _wait_page_ready(page)
        page.wait_for_timeout(2000)
        _dismiss_popup(page)
        _scroll_full_page(page)
        _dismiss_popup(page)
        _wait_images_loaded(page)

        out_dir = get_output_dir(mode, "desktop", slug)
        os.makedirs(out_dir, exist_ok=True)

        _screenshot_full_page_stable(page, os.path.join(out_dir, f"{mode}-desktop-{slug}-screenshot.png"), timeout=60000)
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
        browser = p.chromium.launch(headless=is_headless)
        pixel5 = p.devices["Pixel 5"]
        page = browser.new_page(**pixel5)

        _goto_with_retry(page, url)
        _wait_page_ready(page)
        page.wait_for_timeout(2000)
        _dismiss_popup(page)
        _scroll_full_page(page)
        _dismiss_popup(page)
        _wait_images_loaded(page)

        out_dir = get_output_dir(mode, "android", slug)
        os.makedirs(out_dir, exist_ok=True)

        _screenshot_full_page_stable(page, os.path.join(out_dir, f"{mode}-android-{slug}-screenshot.png"), timeout=60000)
        print(f"  Screenshot saved.")

        with open(os.path.join(out_dir, f"{mode}-android-{slug}-page.html"), "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"  HTML saved.")

        elements = extract_elements(page, device_scale_factor=pixel5["device_scale_factor"])
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
        browser = p.chromium.launch(headless=is_headless)
        iphone13mini = p.devices["iPhone 13 Mini"]
        page = browser.new_page(**iphone13mini)

        _goto_with_retry(page, url)
        _wait_page_ready(page)
        page.wait_for_timeout(2000)
        _dismiss_popup(page)
        _scroll_full_page(page)
        _dismiss_popup(page)
        _wait_images_loaded(page)

        out_dir = get_output_dir(mode, "ios", slug)
        os.makedirs(out_dir, exist_ok=True)

        _screenshot_full_page_stable(page, os.path.join(out_dir, f"{mode}-ios-{slug}-screenshot.png"), timeout=60000)
        print(f"  Screenshot saved.")

        with open(os.path.join(out_dir, f"{mode}-ios-{slug}-page.html"), "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"  HTML saved.")

        elements = extract_elements(page, device_scale_factor=iphone13mini["device_scale_factor"])
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
    print(f"\nDone. Output saved to data/{args.mode}/{args.slug}/[device]/")