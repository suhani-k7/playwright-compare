import os
import json
import time
import threading
from pathlib import Path
from typing import Dict, Any, List
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMPARISONS_DIR = PROJECT_ROOT / "comparisons"
COMPARISONS_DIR.mkdir(exist_ok=True)

# Viewport definitions
DESKTOP_VIEWPORT = {"width": 1440, "height": 900}

# Popup Selectors to scan
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

def get_css_selector_js() -> str:
    return """
    function getCssSelector(el) {
        if (el.id) return '#' + el.id;
        const path = [];
        while (el && el.nodeType === Node.ELEMENT_NODE) {
            let selector = el.nodeName.toLowerCase();
            let sibling = el.previousElementSibling;
            let siblingCount = 1;
            while (sibling) {
                if (sibling.nodeName === el.nodeName) {
                    siblingCount++;
                }
                sibling = sibling.previousElementSibling;
            }
            selector += `:nth-of-type(${siblingCount})`;
            path.unshift(selector);
            el = el.parentNode;
        }
        return path.join(' > ');
    }
    """

def get_bbox_js() -> str:
    return """
    function getBBox(el) {
        const rect = el.getBoundingClientRect();
        return {
            x: rect.x + window.scrollX,
            y: rect.y + window.scrollY,
            width: rect.width,
            height: rect.height
        };
    }
    """

def get_extract_elements_js(popup_selector: str = None, only_sticky: bool = False) -> str:
    popup_sel_arg = f"'{popup_selector}'" if popup_selector else "null"
    only_sticky_arg = "true" if only_sticky else "false"
    
    return f"""
    () => {{
        {get_css_selector_js()}
        {get_bbox_js()}

        function isStickyOrFixed(el) {{
            const viewport_w = window.innerWidth;
            const viewport_h = window.innerHeight;
            let cur = el;
            while (cur && cur !== document.documentElement && cur !== null) {{
                const style = window.getComputedStyle(cur);
                if (style.position === 'sticky' || style.position === 'fixed') {{
                    const rect = cur.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {{
                        const isFullPage = (rect.width >= viewport_w * 0.9) && (rect.height >= viewport_h * 0.9);
                        if (!isFullPage) {{
                            return true;
                        }}
                    }}
                }}
                cur = cur.parentElement;
            }}
            return false;
        }}

        const popupSelector = {popup_sel_arg};
        const onlySticky = {only_sticky_arg};
        
        const root = popupSelector ? document.querySelector(popupSelector) : document;
        if (popupSelector && !root) {{
            return {{ headings: [], images: [], links: [], body_font: "" }};
        }}

        const headings = [];
        for (let level = 1; level <= 6; level++) {{
            const tag = 'h' + level;
            root.querySelectorAll(tag).forEach(el => {{
                const bbox = getBBox(el);
                if (bbox.width > 0 && bbox.height > 0) {{
                    const stickyCheck = !onlySticky || isStickyOrFixed(el);
                    if (stickyCheck) {{
                        headings.push({{
                            tag: tag,
                            id: el.id || '',
                            name: el.getAttribute('name') || '',
                            text: (el.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 80),
                            selector: getCssSelector(el),
                            bbox: bbox,
                            font_family: window.getComputedStyle(el).fontFamily
                        }});
                    }}
                }}
            }});
        }}

        const images = [];
        root.querySelectorAll('img').forEach(el => {{
            const bbox = getBBox(el);
            if (bbox.width > 0 && bbox.height > 0) {{
                const stickyCheck = !onlySticky || isStickyOrFixed(el);
                if (stickyCheck) {{
                    images.push({{
                        tag: 'img',
                        id: el.id || '',
                        name: el.getAttribute('name') || '',
                        alt: el.alt || el.getAttribute('alt') || '',
                        src: el.getAttribute('src') || '',
                        selector: getCssSelector(el),
                        bbox: bbox,
                        width: el.naturalWidth || bbox.width,
                        height: el.naturalHeight || bbox.height
                    }});
                }}
            }}
        }});

        const links = [];
        root.querySelectorAll('a').forEach(el => {{
            const bbox = getBBox(el);
            if (bbox.width > 0 && bbox.height > 0) {{
                const stickyCheck = !onlySticky || isStickyOrFixed(el);
                if (stickyCheck) {{
                    links.push({{
                        tag: 'a',
                        id: el.id || '',
                        name: el.getAttribute('name') || '',
                        href: el.getAttribute('href') || '',
                        text: (el.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 80),
                        selector: getCssSelector(el),
                        bbox: bbox
                    }});
                }}
            }}
        }});

        // Get body font family
        const body_font = window.getComputedStyle(document.body).fontFamily;

        return {{ headings, images, links, body_font }};
    }}
    """

def get_seo_data_js() -> str:
    return """
    () => {
        const titleEl = document.querySelector("title");
        const descEl = document.querySelector("meta[name='description']");
        const keysEl = document.querySelector("meta[name='keywords']");
        const canonicalEl = document.querySelector("link[rel='canonical']");
        
        const ogTags = {};
        ["og:title", "og:description", "og:image", "og:url", "og:type"].forEach(prop => {
            const el = document.querySelector(`meta[property='${prop}']`);
            if (el) {
                ogTags[prop] = el.getAttribute("content") || "";
            }
        });

        const twitterTags = {};
        ["twitter:card", "twitter:title", "twitter:description", "twitter:image"].forEach(prop => {
            const el = document.querySelector(`meta[name='${prop}']`) || document.querySelector(`meta[property='${prop}']`);
            if (el) {
                twitterTags[prop] = el.getAttribute("content") || "";
            }
        });

        // Get hreflangs
        const hreflangs = {};
        document.querySelectorAll("link[rel='alternate'][hreflang]").forEach(el => {
            const lang = el.getAttribute("hreflang");
            const href = el.getAttribute("href");
            if (lang && href) {
                hreflangs[lang] = href;
            }
        });

        return {
            title: titleEl ? (titleEl.textContent || "").trim() : "",
            description: descEl ? (descEl.getAttribute("content") || "").trim() : "",
            keywords: keysEl ? (keysEl.getAttribute("content") || "").trim() : "",
            canonical: canonicalEl ? (canonicalEl.getAttribute("href") || "").trim() : "",
            og: ogTags,
            twitter: twitterTags,
            hreflangs: hreflangs
        };
    }
    """

def _is_likely_popup(page, box, viewport_w, viewport_h) -> bool:
    if not box:
        return False
    w, h = box["width"], box["height"]
    area_ratio = (w * h) / (viewport_w * viewport_h)
    # At least 200x150 and covers >=5% of viewport
    if w < 200 or h < 150:
        return False
    if area_ratio < 0.05:
        return False
    return True

def _has_modal_traits(page, selector: str) -> bool:
    try:
        return page.evaluate("""(sel) => {
            const el = document.querySelector(sel);
            if (!el) return false;

            const hasClose = !!(
                el.querySelector('button[aria-label*="close" i]') ||
                el.querySelector('button[aria-label*="Close"]') ||
                el.querySelector('[class*="close"]') ||
                (el.querySelector('button') &&
                Array.from(el.querySelectorAll('button')).some(b =>
                    (b.textContent || '').trim() === '×' ||
                    (b.textContent || '').trim().toLowerCase() === 'close' ||
                    (b.textContent || '').trim() === '✕'
                ))
            );

            const hasForm = !!(
                el.querySelector('input[type="text"], input[type="email"], input[type="tel"], input[type="number"], textarea, select') ||
                el.querySelector('form')
            );

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

            const style = window.getComputedStyle(el);
            const zIndex = parseInt(style.zIndex) || 0;
            const highZ = zIndex > 100;

            const score = (hasClose ? 1 : 0) + (hasForm ? 1 : 0) + (hasBackdrop ? 1 : 0) + (highZ ? 1 : 0);
            return score >= 1;
        }""", selector)
    except Exception:
        return True

def _scan_for_popup(page, viewport_w, viewport_h):
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

def crawl_viewport_mode(
    run_id: str,
    viewport_name: str,
    mode: str,
    url: str,
    results_dict: dict,
    errors_dict: dict,
    lock: threading.Lock
):
    """
    Crawls a single viewport + URL in a dedicated Playwright instance.
    Runs in parallel inside a thread.
    """
    print(f"[crawler] Starting thread for viewport={viewport_name}, mode={mode}, url={url}")
    run_dir = COMPARISONS_DIR / run_id
    viewport_dir = run_dir / viewport_name
    viewport_dir.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            # 1. Initialize browser/page based on viewport
            if viewport_name == "desktop":
                browser = p.chromium.launch(headless=False)
                page = browser.new_page(viewport=DESKTOP_VIEWPORT)
                viewport_w = DESKTOP_VIEWPORT["width"]
                viewport_h = DESKTOP_VIEWPORT["height"]
            elif viewport_name == "ios":
                browser = p.chromium.launch(headless=False)
                device = p.devices["iPhone 13"]
                page = browser.new_page(**device)
                viewport_w = device["viewport"]["width"]
                viewport_h = device["viewport"]["height"]
            elif viewport_name == "android":
                browser = p.chromium.launch(headless=False)
                device = p.devices["Pixel 5"]
                page = browser.new_page(**device)
                viewport_w = device["viewport"]["width"]
                viewport_h = device["viewport"]["height"]
            else:
                raise ValueError(f"Unknown viewport: {viewport_name}")

            # Go to page
            page.goto(url, wait_until="load", timeout=60000)
            page.wait_for_load_state("networkidle", timeout=10000)

            # 2. Extract SEO / Head Data (Only needed once per mode, but we can do it on fullpage)
            seo_data = page.evaluate(get_seo_data_js())

            # -------------------------------------------------------------
            # SECTION 1: Fullpage
            # -------------------------------------------------------------
            fullpage_dir = viewport_dir / "fullpage"
            fullpage_dir.mkdir(exist_ok=True)
            
            # Save raw screenshot (ref.png or live.png)
            screenshot_path = fullpage_dir / f"{mode}.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            
            # Extract HTML
            html_content = page.content()
            with open(fullpage_dir / f"{mode}-page.html", "w", encoding="utf-8") as f:
                f.write(html_content)
                
            # Extract Elements
            elements = page.evaluate(get_extract_elements_js())
            with open(fullpage_dir / f"{mode}-elements.json", "w", encoding="utf-8") as f:
                json.dump(elements, f, indent=2)

            # -------------------------------------------------------------
            # SECTION 2: First Fold (Viewport only, no scroll)
            # -------------------------------------------------------------
            firstfold_dir = viewport_dir / "firstfold"
            firstfold_dir.mkdir(exist_ok=True)
            
            # Save viewport screenshot
            ff_screenshot_path = firstfold_dir / f"{mode}.png"
            page.screenshot(path=str(ff_screenshot_path), full_page=False, clip={"x": 0, "y": 0, "width": viewport_w, "height": viewport_h})
            
            # Filter elements inside first fold (y < viewport_h)
            ff_elements = {
                "headings": [e for e in elements["headings"] if e["bbox"]["y"] < viewport_h],
                "images": [e for e in elements["images"] if e["bbox"]["y"] < viewport_h],
                "links": [e for e in elements["links"] if e["bbox"]["y"] < viewport_h],
                "body_font": elements["body_font"]
            }
            with open(firstfold_dir / f"{mode}-elements.json", "w", encoding="utf-8") as f:
                json.dump(ff_elements, f, indent=2)

            # -------------------------------------------------------------
            # SECTION 3: Sticky (Scroll to 50% page height)
            # -------------------------------------------------------------
            sticky_dir = viewport_dir / "sticky"
            sticky_dir.mkdir(exist_ok=True)
            
            # Scroll to 50% page height
            page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
            page.wait_for_timeout(500)
            
            # Take viewport screenshot at scrolled position
            sticky_screenshot_path = sticky_dir / f"{mode}.png"
            page.screenshot(path=str(sticky_screenshot_path), full_page=False, clip={"x": 0, "y": 0, "width": viewport_w, "height": viewport_h})
            
            # Extract sticky elements only (they are computed relative to scrolled page)
            sticky_elements = page.evaluate(get_extract_elements_js(only_sticky=True))
            with open(sticky_dir / f"{mode}-elements.json", "w", encoding="utf-8") as f:
                json.dump(sticky_elements, f, indent=2)

            # -------------------------------------------------------------
            # SECTION 4: Popup (Reset scroll, then trigger strategies)
            # -------------------------------------------------------------
            popup_dir = viewport_dir / "popup"
            popup_dir.mkdir(exist_ok=True)
            
            # Reset scroll
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)
            
            popup_bbox = None
            popup_sel = None

            # Strategy 1: auto-appear
            for attempt in range(10):
                popup_bbox, popup_sel = _scan_for_popup(page, viewport_w, viewport_h)
                if popup_bbox:
                    break
                page.wait_for_timeout(500)

            # Strategy 2: Scroll trigger
            if not popup_bbox:
                page.evaluate("window.scrollBy(0, 500)")
                page.wait_for_timeout(1000)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(1000)
                popup_bbox, popup_sel = _scan_for_popup(page, viewport_w, viewport_h)

            # Strategy 3: Tab visibility switch
            if not popup_bbox:
                page.evaluate("""() => {
                    Object.defineProperty(document, 'hidden', {value: true, writable: true, configurable: true});
                    Object.defineProperty(document, 'visibilityState', {value: 'hidden', writable: true, configurable: true});
                    document.dispatchEvent(new Event('visibilitychange'));
                    window.dispatchEvent(new Event('blur'));
                }""")
                page.wait_for_timeout(1000)
                page.evaluate("""() => {
                    Object.defineProperty(document, 'hidden', {value: false, writable: true, configurable: true});
                    Object.defineProperty(document, 'visibilityState', {value: 'visible', writable: true, configurable: true});
                    document.dispatchEvent(new Event('visibilitychange'));
                    window.dispatchEvent(new Event('focus'));
                }""")
                page.wait_for_timeout(1000)
                popup_bbox, popup_sel = _scan_for_popup(page, viewport_w, viewport_h)

            # Strategy 4: Exit intent mouse move
            if not popup_bbox:
                page.mouse.move(viewport_w // 2, 5)
                page.wait_for_timeout(500)
                page.mouse.move(viewport_w // 2, 0)
                page.wait_for_timeout(1000)
                popup_bbox, popup_sel = _scan_for_popup(page, viewport_w, viewport_h)

            # Strategy 5: Longer passive wait (10s more)
            if not popup_bbox:
                for attempt in range(10):
                    popup_bbox, popup_sel = _scan_for_popup(page, viewport_w, viewport_h)
                    if popup_bbox:
                        break
                    page.wait_for_timeout(1000)

            # Screenshot popup
            popup_screenshot_path = popup_dir / f"{mode}.png"
            if popup_bbox:
                padding = 10
                clip = {
                    "x": max(0, popup_bbox["x"] - padding),
                    "y": max(0, popup_bbox["y"] - padding),
                    "width": min(viewport_w - max(0, popup_bbox["x"] - padding), popup_bbox["width"] + padding * 2),
                    "height": min(viewport_h - max(0, popup_bbox["y"] - padding), popup_bbox["height"] + padding * 2),
                }
            else:
                clip = {"x": 0, "y": 0, "width": viewport_w, "height": viewport_h}

            page.screenshot(path=str(popup_screenshot_path), full_page=False, clip=clip)
            
            # Extract elements in popup container
            popup_elements = page.evaluate(get_extract_elements_js(popup_selector=popup_sel))
            popup_elements["screenshot_offset"] = {
                "x": clip["x"],
                "y": clip["y"]
            }
            with open(popup_dir / f"{mode}-elements.json", "w", encoding="utf-8") as f:
                json.dump(popup_elements, f, indent=2)

            browser.close()

            # Save thread results
            with lock:
                if viewport_name not in results_dict:
                    results_dict[viewport_name] = {}
                results_dict[viewport_name][mode] = {
                    "seo": seo_data,
                    "popup_found": popup_bbox is not None
                }
                
    except Exception as e:
        print(f"[crawler] ERROR in viewport={viewport_name}, mode={mode}: {e}")
        with lock:
            errors_dict[f"{viewport_name}_{mode}"] = str(e)

def run_crawler_parallel(run_id: str, reference_url: str, live_url: str) -> dict:
    """
    Spawns parallel crawler threads to scrape reference and live URLs.
    """
    results = {}
    errors = {}
    lock = threading.Lock()

    threads = []
    tasks = [
        ("desktop", "reference", reference_url),
        ("desktop", "live", live_url),
        ("ios", "reference", reference_url),
        ("ios", "live", live_url),
        ("android", "reference", reference_url),
        ("android", "live", live_url),
    ]

    for viewport, mode, url in tasks:
        t = threading.Thread(
            target=crawl_viewport_mode,
            args=(run_id, viewport, mode, url, results, errors, lock)
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    if errors:
        raise RuntimeError(f"Crawl failures encountered: {json.dumps(errors)}")

    return results
