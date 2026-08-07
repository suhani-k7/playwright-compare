import os
import json
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any, Tuple
import time

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMPARISONS_DIR = PROJECT_ROOT / "comparisons"

def _normalize_url(href: str, base_url: str = "") -> str:
    href = href.strip()
    if not href or href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
        return href
    if base_url:
        href = urllib.parse.urljoin(base_url, href)
    parsed = urllib.parse.urlparse(href)
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    query = parsed.query
    
    # Strip domains for internal comparison to reduce environment noise
    internal_domains = ["axismaxlife.com", "neouat.axismaxlife.com"]
    is_internal = any(netloc.endswith(d) for d in internal_domains)
    if is_internal or not netloc:
        normalized = path
    else:
        normalized = f"{parsed.scheme.lower()}://{netloc}{path}"
    
    if query:
        normalized = f"{normalized}?{query}"
    return normalized

def match_elements(ref_list: List[dict], live_list: List[dict], base_ref_url: str, base_live_url: str) -> Tuple[List[Tuple[dict, dict, str]], List[dict], List[dict]]:
    matched_pairs = []
    unmatched_ref = list(ref_list)
    unmatched_live = list(live_list)

    # Helper to check semantic matching
    # 1. Matching by non-empty ID
    i = 0
    while i < len(unmatched_ref):
        ref_el = unmatched_ref[i]
        ref_id = ref_el.get("id")
        found = False
        if ref_id:
            for j, live_el in enumerate(unmatched_live):
                if live_el.get("tag") == ref_el.get("tag") and live_el.get("id") == ref_id:
                    matched_pairs.append((ref_el, live_el, "semantic"))
                    unmatched_live.pop(j)
                    unmatched_ref.pop(i)
                    found = True
                    break
        if not found:
            i += 1

    # 2. Matching by Name attribute
    i = 0
    while i < len(unmatched_ref):
        ref_el = unmatched_ref[i]
        ref_name = ref_el.get("name")
        found = False
        if ref_name:
            for j, live_el in enumerate(unmatched_live):
                if live_el.get("tag") == ref_el.get("tag") and live_el.get("name") == ref_name:
                    matched_pairs.append((ref_el, live_el, "semantic"))
                    unmatched_live.pop(j)
                    unmatched_ref.pop(i)
                    found = True
                    break
        if not found:
            i += 1

    # 3. Matching by specific stable attributes
    # - img: normalized src path
    # - a: normalized href
    # - headings: text content (minimum length of 3 to avoid short noise)
    i = 0
    while i < len(unmatched_ref):
        ref_el = unmatched_ref[i]
        tag = ref_el.get("tag")
        found = False
        
        for j, live_el in enumerate(unmatched_live):
            if live_el.get("tag") != tag:
                continue
                
            is_match = False
            if tag == "img":
                r_src = _normalize_url(ref_el.get("src", ""), base_ref_url)
                l_src = _normalize_url(live_el.get("src", ""), base_live_url)
                if r_src and l_src and r_src == l_src:
                    is_match = True
            elif tag == "a":
                r_href = _normalize_url(ref_el.get("href", ""), base_ref_url)
                l_href = _normalize_url(live_el.get("href", ""), base_live_url)
                if r_href and l_href and r_href == l_href:
                    is_match = True
            else: # headings
                r_text = ref_el.get("text", "").strip()
                l_text = live_el.get("text", "").strip()
                if r_text and l_text and r_text == l_text and len(r_text) > 3:
                    is_match = True
                    
            if is_match:
                matched_pairs.append((ref_el, live_el, "semantic"))
                unmatched_live.pop(j)
                unmatched_ref.pop(i)
                found = True
                break
        if not found:
            i += 1

    # 4. Fallback: Positional matching via exact CSS selector path
    i = 0
    while i < len(unmatched_ref):
        ref_el = unmatched_ref[i]
        ref_sel = ref_el.get("selector")
        found = False
        if ref_sel:
            for j, live_el in enumerate(unmatched_live):
                if live_el.get("tag") == ref_el.get("tag") and live_el.get("selector") == ref_sel:
                    matched_pairs.append((ref_el, live_el, "positional"))
                    unmatched_live.pop(j)
                    unmatched_ref.pop(i)
                    found = True
                    break
        if not found:
            i += 1

    # 5. Fallback: Sequential tag-order matching (first unmatched matches first unmatched live of same tag)
    tags = set(e.get("tag") for e in unmatched_ref)
    for t in tags:
        ref_tag_els = [e for e in unmatched_ref if e.get("tag") == t]
        live_tag_els = [e for e in unmatched_live if e.get("tag") == t]
        for r_el, l_el in zip(ref_tag_els, live_tag_els):
            matched_pairs.append((r_el, l_el, "positional"))
            unmatched_ref.remove(r_el)
            unmatched_live.remove(l_el)

    return matched_pairs, unmatched_ref, unmatched_live

def diff_elements(
    category: str,
    ref_list: List[dict],
    live_list: List[dict],
    base_ref_url: str,
    base_live_url: str
) -> List[dict]:
    issues = []
    
    # Run element alignment
    matched, unmatched_ref, unmatched_live = match_elements(
        ref_list, live_list, base_ref_url, base_live_url
    )

    # 1. Report Missing elements (things in ref, missing in live)
    for r_el in unmatched_ref:
        ref_val = ""
        if category == "headings":
            ref_val = f"{r_el.get('tag').upper()}: {r_el.get('text')}"
        elif category == "images":
            ref_val = f"Alt: {r_el.get('alt')} (Src: {r_el.get('src')})"
        elif category == "links":
            ref_val = f"Text: {r_el.get('text')} (Href: {r_el.get('href')})"

        issues.append({
            "category": category,
            "type": "missing",
            "element": r_el.get("tag"),
            "selector": r_el.get("selector"),
            "refValue": ref_val,
            "liveValue": "",
            "boundingBox": r_el.get("bbox"),
            "matchedBy": None
        })

    # 2. Report Extra elements (things in live, not in ref)
    for l_el in unmatched_live:
        live_val = ""
        if category == "headings":
            live_val = f"{l_el.get('tag').upper()}: {l_el.get('text')}"
        elif category == "images":
            live_val = f"Alt: {l_el.get('alt')} (Src: {l_el.get('src')})"
        elif category == "links":
            live_val = f"Text: {l_el.get('text')} (Href: {l_el.get('href')})"

        issues.append({
            "category": category,
            "type": "extra",
            "element": l_el.get("tag"),
            "selector": l_el.get("selector"),
            "refValue": "",
            "liveValue": live_val,
            "boundingBox": l_el.get("bbox"),
            "matchedBy": None
        })

    # 3. Report differences in matched pairs
    for r_el, l_el, strategy in matched:
        tag = r_el.get("tag")
        
        if category == "headings":
            # Heading text mismatch
            if r_el.get("text") != l_el.get("text"):
                issues.append({
                    "category": "headings",
                    "type": "content-mismatch",
                    "element": tag,
                    "selector": l_el.get("selector"),
                    "refValue": r_el.get("text"),
                    "liveValue": l_el.get("text"),
                    "boundingBox": l_el.get("bbox"),
                    "matchedBy": strategy
                })
            # Heading level change (e.g. h1 -> h2)
            if r_el.get("tag") != l_el.get("tag"):
                issues.append({
                    "category": "headings",
                    "type": "attribute-mismatch",
                    "element": tag,
                    "selector": l_el.get("selector"),
                    "refValue": r_el.get("tag"),
                    "liveValue": l_el.get("tag"),
                    "boundingBox": l_el.get("bbox"),
                    "matchedBy": strategy
                })
            # Computed font family mismatch
            r_font = r_el.get("font_family", "").strip()
            l_font = l_el.get("font_family", "").strip()
            if r_font and l_font and r_font != l_font:
                issues.append({
                    "category": "headings",
                    "type": "attribute-mismatch",
                    "element": tag,
                    "selector": l_el.get("selector"),
                    "refValue": f"Font: {r_font}",
                    "liveValue": f"Font: {l_font}",
                    "boundingBox": l_el.get("bbox"),
                    "matchedBy": strategy
                })

        elif category == "images":
            # Image Alt tag mismatch
            if r_el.get("alt") != l_el.get("alt"):
                issues.append({
                    "category": "images",
                    "type": "attribute-mismatch",
                    "element": "img",
                    "selector": l_el.get("selector"),
                    "refValue": f"Alt: {r_el.get('alt')}",
                    "liveValue": f"Alt: {l_el.get('alt')}",
                    "boundingBox": l_el.get("bbox"),
                    "matchedBy": strategy
                })
            # Image Src mismatch (normalized)
            r_src_norm = _normalize_url(r_el.get("src", ""), base_ref_url)
            l_src_norm = _normalize_url(l_el.get("src", ""), base_live_url)
            if r_src_norm != l_src_norm:
                issues.append({
                    "category": "images",
                    "type": "attribute-mismatch",
                    "element": "img",
                    "selector": l_el.get("selector"),
                    "refValue": f"Src: {r_el.get('src')}",
                    "liveValue": f"Src: {l_el.get('src')}",
                    "boundingBox": l_el.get("bbox"),
                    "matchedBy": strategy
                })
            # Dimensions check
            ref_w, ref_h = r_el.get("width", 0), r_el.get("height", 0)
            live_w, live_h = l_el.get("width", 0), l_el.get("height", 0)
            if abs(ref_w - live_w) > 5 or abs(ref_h - live_h) > 5:
                issues.append({
                    "category": "images",
                    "type": "attribute-mismatch",
                    "element": "img",
                    "selector": l_el.get("selector"),
                    "refValue": f"Dim: {ref_w}x{ref_h}",
                    "liveValue": f"Dim: {live_w}x{live_h}",
                    "boundingBox": l_el.get("bbox"),
                    "matchedBy": strategy
                })

        elif category == "links":
            # Link Href mismatch
            r_href_norm = _normalize_url(r_el.get("href", ""), base_ref_url)
            l_href_norm = _normalize_url(l_el.get("href", ""), base_live_url)
            if r_href_norm != l_href_norm:
                issues.append({
                    "category": "links",
                    "type": "attribute-mismatch",
                    "element": "a",
                    "selector": l_el.get("selector"),
                    "refValue": f"Href: {r_el.get('href')}",
                    "liveValue": f"Href: {l_el.get('href')}",
                    "boundingBox": l_el.get("bbox"),
                    "matchedBy": strategy
                })
            # Anchor Text mismatch
            if r_el.get("text") != l_el.get("text"):
                issues.append({
                    "category": "links",
                    "type": "content-mismatch",
                    "element": "a",
                    "selector": l_el.get("selector"),
                    "refValue": r_el.get("text"),
                    "liveValue": l_el.get("text"),
                    "boundingBox": l_el.get("bbox"),
                    "matchedBy": strategy
                })

    return issues

def diff_seo(ref_seo: dict, live_seo: dict) -> List[dict]:
    """
    Diffs SEO head elements. Since these don't have bboxes, boundingBox is None.
    """
    issues = []
    
    # Title
    if ref_seo.get("title") != live_seo.get("title"):
        issues.append({
            "category": "metadata",
            "type": "content-mismatch",
            "element": "title",
            "selector": "title",
            "refValue": ref_seo.get("title"),
            "liveValue": live_seo.get("title"),
            "boundingBox": None,
            "matchedBy": "semantic"
        })

    # Description
    if ref_seo.get("description") != live_seo.get("description"):
        issues.append({
            "category": "metadata",
            "type": "content-mismatch",
            "element": "meta[name='description']",
            "selector": "meta[name='description']",
            "refValue": ref_seo.get("description"),
            "liveValue": live_seo.get("description"),
            "boundingBox": None,
            "matchedBy": "semantic"
        })

    # Keywords
    if ref_seo.get("keywords") != live_seo.get("keywords"):
        issues.append({
            "category": "metadata",
            "type": "content-mismatch",
            "element": "meta[name='keywords']",
            "selector": "meta[name='keywords']",
            "refValue": ref_seo.get("keywords"),
            "liveValue": live_seo.get("keywords"),
            "boundingBox": None,
            "matchedBy": "semantic"
        })

    # Canonical
    r_canon = _normalize_url(ref_seo.get("canonical", ""))
    l_canon = _normalize_url(live_seo.get("canonical", ""))
    if r_canon != l_canon:
        issues.append({
            "category": "metadata",
            "type": "attribute-mismatch",
            "element": "link[rel='canonical']",
            "selector": "link[rel='canonical']",
            "refValue": ref_seo.get("canonical"),
            "liveValue": live_seo.get("canonical"),
            "boundingBox": None,
            "matchedBy": "semantic"
        })

    # Open Graph Tags (diff each og:* tag independently)
    ref_og = ref_seo.get("og", {})
    live_og = live_seo.get("og", {})
    og_keys = set(ref_og.keys()) | set(live_og.keys())
    for k in og_keys:
        r_val = ref_og.get(k, "")
        l_val = live_og.get(k, "")
        if r_val != l_val:
            issues.append({
                "category": "metadata",
                "type": "attribute-mismatch",
                "element": f"meta[property='{k}']",
                "selector": f"meta[property='{k}']",
                "refValue": r_val,
                "liveValue": l_val,
                "boundingBox": None,
                "matchedBy": "semantic"
            })

    # Twitter Card Tags
    ref_tw = ref_seo.get("twitter", {})
    live_tw = live_seo.get("twitter", {})
    tw_keys = set(ref_tw.keys()) | set(live_tw.keys())
    for k in tw_keys:
        r_val = ref_tw.get(k, "")
        l_val = live_tw.get(k, "")
        if r_val != l_val:
            issues.append({
                "category": "metadata",
                "type": "attribute-mismatch",
                "element": f"meta[property/name='{k}']",
                "selector": f"meta[name='{k}']",
                "refValue": r_val,
                "liveValue": l_val,
                "boundingBox": None,
                "matchedBy": "semantic"
            })

    # Hreflangs
    ref_hl = ref_seo.get("hreflangs", {})
    live_hl = live_seo.get("hreflangs", {})
    hl_langs = set(ref_hl.keys()) | set(live_hl.keys())
    for lang in hl_langs:
        r_href = ref_hl.get(lang, "")
        l_href = live_hl.get(lang, "")
        if r_href != l_href:
            issues.append({
                "category": "metadata",
                "type": "attribute-mismatch",
                "element": f"link[rel='alternate'][hreflang='{lang}']",
                "selector": f"link[hreflang='{lang}']",
                "refValue": r_href,
                "liveValue": l_href,
                "boundingBox": None,
                "matchedBy": "semantic"
            })

    return issues

def run_diff_engine(run_id: str, crawl_results: dict, reference_url: str, live_url: str):
    """
    Reads element JSONs from directory structure, runs diffs,
    and outputs diff.json files and meta.json.
    """
    run_dir = COMPARISONS_DIR / run_id

    # 1. Output meta.json
    meta = {
        "run_id": run_id,
        "reference_url": reference_url,
        "live_url": live_url,
        "timestamp": time.time(),
        "categories_run": ["headings", "images", "links", "metadata", "sticky", "popup"]
    }
    with open(run_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    # 2. Compare SEO across desktop fullpage (SEO is identical across viewports, run once)
    seo_dir = run_dir / "seo"
    seo_dir.mkdir(exist_ok=True)
    
    desktop_ref_seo = crawl_results.get("desktop", {}).get("reference", {}).get("seo", {})
    desktop_live_seo = crawl_results.get("desktop", {}).get("live", {}).get("seo", {})
    seo_issues = diff_seo(desktop_ref_seo, desktop_live_seo)
    
    seo_diff = {
        "category": "seo",
        "viewport": "global",
        "section": "head",
        "issues": seo_issues
    }
    with open(seo_dir / "diff.json", "w", encoding="utf-8") as f:
        json.dump(seo_diff, f, indent=2)

    # 3. Compare Viewports & Sections
    viewports = ["desktop", "ios", "android"]
    sections = ["fullpage", "firstfold", "sticky", "popup"]

    for vp in viewports:
        for sec in sections:
            sec_dir = run_dir / vp / sec
            sec_dir.mkdir(parents=True, exist_ok=True)

            ref_el_file = sec_dir / "reference-elements.json"
            live_el_file = sec_dir / "live-elements.json"

            # Fallbacks in case folders don't have files
            if not ref_el_file.exists() or not live_el_file.exists():
                # Write empty diff.json
                with open(sec_dir / "diff.json", "w", encoding="utf-8") as f:
                    json.dump({"viewport": vp, "section": sec, "issues": []}, f, indent=2)
                continue

            with open(ref_el_file, "r", encoding="utf-8") as f:
                ref_elements = json.load(f)
            with open(live_el_file, "r", encoding="utf-8") as f:
                live_elements = json.load(f)

            # BBox adjustment for popups: elements coordinates inside popup must be relative
            # to the cropped popup screenshot.
            if sec == "popup" and ref_elements.get("screenshot_offset") and live_elements.get("screenshot_offset"):
                ref_offset = ref_elements["screenshot_offset"]
                for cat in ["headings", "images", "links"]:
                    for item in ref_elements.get(cat, []):
                        if item.get("bbox"):
                            item["bbox"]["x"] -= ref_offset["x"]
                            item["bbox"]["y"] -= ref_offset["y"]

                live_offset = live_elements["screenshot_offset"]
                for cat in ["headings", "images", "links"]:
                    for item in live_elements.get(cat, []):
                        if item.get("bbox"):
                            item["bbox"]["x"] -= live_offset["x"]
                            item["bbox"]["y"] -= live_offset["y"]

            section_issues = []

            # Compare Headings
            heading_issues = diff_elements("headings", ref_elements.get("headings", []), live_elements.get("headings", []), reference_url, live_url)
            section_issues.extend(heading_issues)

            # Compare Images
            image_issues = diff_elements("images", ref_elements.get("images", []), live_elements.get("images", []), reference_url, live_url)
            section_issues.extend(image_issues)

            # Compare Links
            link_issues = diff_elements("links", ref_elements.get("links", []), live_elements.get("links", []), reference_url, live_url)
            section_issues.extend(link_issues)

            # Font-stack checks: check body font stacks
            ref_body_font = ref_elements.get("body_font", "")
            live_body_font = live_elements.get("body_font", "")
            if ref_body_font and live_body_font and ref_body_font != live_body_font:
                section_issues.append({
                    "category": "metadata",
                    "type": "attribute-mismatch",
                    "element": "body",
                    "selector": "body",
                    "refValue": f"Font Stack: {ref_body_font}",
                    "liveValue": f"Font Stack: {live_body_font}",
                    "boundingBox": None,
                    "matchedBy": "semantic"
                })

            # Check sticky count/presence if this is the sticky section
            if sec == "sticky":
                # Check sticky presence and difference
                ref_sticky = ref_elements.get("headings", []) + ref_elements.get("images", []) + ref_elements.get("links", [])
                live_sticky = live_elements.get("headings", []) + live_elements.get("images", []) + live_elements.get("links", [])
                if len(ref_sticky) != len(live_sticky):
                    section_issues.append({
                        "category": "sticky",
                        "type": "attribute-mismatch",
                        "element": "sticky-elements",
                        "selector": "window",
                        "refValue": f"Sticky elements count: {len(ref_sticky)}",
                        "liveValue": f"Sticky elements count: {len(live_sticky)}",
                        "boundingBox": None,
                        "matchedBy": "semantic"
                    })

            # Check popup presence if this is the popup section
            if sec == "popup":
                ref_popup_found = crawl_results.get(vp, {}).get("reference", {}).get("popup_found", False)
                live_popup_found = crawl_results.get(vp, {}).get("live", {}).get("popup_found", False)
                if ref_popup_found != live_popup_found:
                    section_issues.append({
                        "category": "popup",
                        "type": "attribute-mismatch",
                        "element": "popup-modal",
                        "selector": "window",
                        "refValue": f"Popup present: {ref_popup_found}",
                        "liveValue": f"Popup present: {live_popup_found}",
                        "boundingBox": None,
                        "matchedBy": "semantic"
                    })

            # Write diff.json
            diff_out = {
                "viewport": vp,
                "section": sec,
                "issues": section_issues
            }
            with open(sec_dir / "diff.json", "w", encoding="utf-8") as f:
                json.dump(diff_out, f, indent=2)

            # Cleanup temp element files so we only keep static assets
            try:
                os.remove(ref_el_file)
                os.remove(live_el_file)
                # We can also delete page.html if wanted, but let's keep it or delete it. Let's delete it.
                ref_html = sec_dir / "reference-page.html"
                live_html = sec_dir / "live-page.html"
                if ref_html.exists(): os.remove(ref_html)
                if live_html.exists(): os.remove(live_html)
            except Exception:
                pass
