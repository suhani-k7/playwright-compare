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
    """Extract only elements that are inside sticky/fixed elements."""
    return page.evaluate("""
        () => {
            function isStickyOrFixed(el) {
                const viewport_w = window.innerWidth;
                const viewport_h = window.innerHeight;
                
                let cur = el;
                while (cur && cur !== document.documentElement && cur !== null) {
                    const style = window.getComputedStyle(cur);
                    if (style.position === 'sticky' || style.position === 'fixed') {
                        const rect = cur.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            const isFullPage = (rect.width >= viewport_w * 0.9) && (rect.height >= viewport_h * 0.9);
                            if (!isFullPage) {
                                return true;
                            }
                        }
                    }
                    cur = cur.parentElement;
                }
                return false;
            }

            function getBBox(el) {
                const rect = el.getBoundingClientRect();
                return {
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height
                };
            }

            const headings = [];
            for (let level = 1; level <= 6; level++) {
                const tag = 'h' + level;
                document.querySelectorAll(tag).forEach(el => {
                    const bbox = getBBox(el);
                    if (bbox.width > 0 && bbox.height > 0 && isStickyOrFixed(el)) {
                        headings.push({
                            tag: tag,
                            text: (el.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 80),
                            bbox: bbox
                        });
                    }
                });
            }

            const images = [];
            document.querySelectorAll('img').forEach(el => {
                const bbox = getBBox(el);
                if (bbox.width > 0 && bbox.height > 0 && isStickyOrFixed(el)) {
                    images.push({
                        alt: el.alt || el.getAttribute('alt') || '',
                        src: el.getAttribute('src') || '',
                        bbox: bbox
                    });
                }
            });

            const buttons = [];
            const seenButtons = new Set();
            const buttonSelectors = ['button', 'input[type="button"]', 'input[type="submit"]', '[role="button"]'];
            buttonSelectors.forEach(selector => {
                document.querySelectorAll(selector).forEach(el => {
                    if (seenButtons.has(el)) return;
                    const bbox = getBBox(el);
                    if (bbox.width > 0 && bbox.height > 0 && isStickyOrFixed(el)) {
                        seenButtons.add(el);
                        let href = el.getAttribute('href') || '';
                        if (!href) {
                            let a = el.closest('a');
                            if (a) href = a.getAttribute('href') || '';
                        }
                        if (!href) {
                            let d = el.querySelector('a');
                            if (d) href = d.getAttribute('href') || '';
                        }
                        buttons.push({
                            selector: selector,
                            text: (el.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 80),
                            aria_label: (el.getAttribute('aria-label') || '').trim(),
                            href: href.trim(),
                            bbox: bbox
                        });
                    }
                });
            });

            const links = [];
            document.querySelectorAll('a').forEach(el => {
                const bbox = getBBox(el);
                if (bbox.width > 0 && bbox.height > 0 && isStickyOrFixed(el)) {
                    links.push({
                        href: el.getAttribute('href') || '',
                        bbox: bbox
                    });
                }
            });

            const canonical = [];
            const canonicalEl = document.querySelector("link[rel='canonical']");
            if (canonicalEl) {
                canonical.push({
                    href: canonicalEl.getAttribute('href') || '',
                    bbox: null
                });
            }

            const meta = [];
            const titleEl = document.querySelector("title");
            if (titleEl) {
                meta.push({ name: "title", value: (titleEl.textContent || "").trim(), bbox: null });
            }
            const descEl = document.querySelector("meta[name='description']");
            if (descEl) {
                meta.push({ name: "description", value: descEl.getAttribute("content") || "", bbox: null });
            }
            const keysEl = document.querySelector("meta[name='keywords']");
            if (keysEl) {
                meta.push({ name: "keywords", value: keysEl.getAttribute("content") || "", bbox: null });
            }

            const og_tags = [];
            ["og:title", "og:description", "og:keywords"].forEach(prop => {
                const el = document.querySelector(`meta[property='${prop}']`);
                if (el) {
                    og_tags.push({ property: prop, value: el.getAttribute("content") || "", bbox: null });
                }
            });

            const sticky = [];
            document.querySelectorAll('*').forEach(el => {
                const style = window.getComputedStyle(el);
                if (style.position === 'sticky' || style.position === 'fixed') {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        const isFullPage = (rect.width >= window.innerWidth * 0.9) && (rect.height >= window.innerHeight * 0.9);
                        if (!isFullPage) {
                            sticky.push({
                                tag: el.tagName.toLowerCase(),
                                bbox: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
                            });
                        }
                    }
                }
            });

            return { headings, images, buttons, links, canonical, meta, og_tags, sticky };
        }
    """)

def capture_sticky(p, browser_kwargs, page_kwargs, url, mode, device, slug):
    fold_height = FIRST_FOLD_HEIGHTS[device]
    browser = p.chromium.launch(**browser_kwargs)
    page = browser.new_page(**page_kwargs)
    page.goto(url, wait_until="load")
    page.wait_for_load_state("networkidle")

    out_dir = get_output_dir(mode, device, slug)
    os.makedirs(out_dir, exist_ok=True)

        # Define scroll percentages (10% to 100% inclusive)
    scroll_points = [i for i in range(10, 101, 10)]
    aggregated_sticky = []
    # Optionally store per‑point element files for debugging
    for pct in scroll_points:
        # Scroll to the desired percentage of total page height
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pct} / 100)")
        # Wait a short time for any JS sticky logic to activate (200‑500 ms)
        page.wait_for_timeout(300)
        # Extract elements at this scroll position
        elements = extract_elements(page, fold_height)
        sticky_here = elements.get("sticky", [])
        aggregated_sticky.extend(sticky_here)
        # Capture screenshot based on mode and sticky presence
        if mode == "live":
            # Live mode: always capture screenshot (JPEG)
            screenshot_name = f"{device}-{slug}-{pct}pctscroll-screenshot.jpg"
            page.screenshot(path=os.path.join(out_dir, screenshot_name), full_page=False, type="jpeg")
            print(f"  Sticky screenshot saved (live-{device}) at {pct}% scroll.")
        else:
            # Reference mode: capture only if sticky elements detected
            if sticky_here:
                screenshot_name = f"{device}-{slug}-{pct}pctscroll-screenshot.png"
                page.screenshot(path=os.path.join(out_dir, screenshot_name), full_page=False)
                print(f"  Sticky screenshot saved (reference-{device}) at {pct}% scroll (sticky detected).")
        # Save per‑point elements file for reference/debug (optional)
        per_point_path = os.path.join(out_dir, f"{mode}-{device}-{slug}-{pct}pct-elements.json")
        with open(per_point_path, "w", encoding="utf-8") as f:
            json.dump(elements, f, indent=2)

    # Remove duplicate sticky entries based on their bounding box & tag
    def norm(el):
        b = el.get("bbox", {})
        return (
            el.get("tag", "").lower(),
            round(b.get("x", 0)),
            round(b.get("y", 0)),
            round(b.get("width", 0)),
            round(b.get("height", 0))
        )
    unique = {norm(e): e for e in aggregated_sticky}
    # Replace sticky list with deduped collection
    final_elements = {
        "sticky": list(unique.values())
    }
    # Save the combined elements JSON used by comparison
    with open(os.path.join(out_dir, f"{mode}-{device}-{slug}-elements.json"), "w", encoding="utf-8") as f:
        json.dump(final_elements, f, indent=2)
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
