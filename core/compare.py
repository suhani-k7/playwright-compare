import argparse
import json
import os
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
import urllib.parse
from difflib import SequenceMatcher
from fold_utils import compare_visual_folds

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
# -------------------------------------------------------------------
# Helpers to load saved capture outputs
# -------------------------------------------------------------------

def _normalize_href(href: str, base_url: str = "", known_domains: list = None) -> str:
    href = href.strip()
    if not href or href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
        return href

    if base_url:
        href = urllib.parse.urljoin(base_url, href)

    parsed = urllib.parse.urlparse(href)
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    query = parsed.query

    # Known internal domains — strip domain, compare path only
    # This prevents prod vs UAT domain differences from creating noise
    internal_domains = known_domains or ["axismaxlife.com", "neouat.axismaxlife.com"]
    is_internal = any(netloc.endswith(d) for d in internal_domains)

    if is_internal or not netloc:
        # Internal or relative — path only
        normalized = path
    else:
        # External link — keep full URL so real external destination changes are caught
        normalized = f"{parsed.scheme.lower()}://{netloc}{path}"

    if query:
        normalized = f"{normalized}?{query}"

    return normalized

def load_html(mode: str, device: str, slug: str) -> BeautifulSoup:
    path = os.path.join(DATA_DIR, mode, f"{device}-{slug}", f"{mode}-{device}-{slug}-page.html")
    if not os.path.exists(path):
        raise FileNotFoundError(f"HTML not found: {path}. Run capture.py first.")
    with open(path, "r", encoding="utf-8") as f:
        return BeautifulSoup(f.read(), "lxml")


def load_elements(mode: str, device: str, slug: str) -> dict:
    path = os.path.join(DATA_DIR, mode, f"{device}-{slug}", f"{mode}-{device}-{slug}-elements.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Elements JSON not found: {path}. Run capture.py first.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# -------------------------------------------------------------------
# Individual comparators
# Each returns:
#   status  — "PASS" or "FAIL"
#   details — list of mismatch dicts (used by annotator in Phase 3)
# -------------------------------------------------------------------

def _normalize_heading_text(text: str) -> str:
    """Lowercase + collapse whitespace so trivial formatting diffs don't count."""
    return " ".join((text or "").strip().lower().split())


def compare_headings(ref_soup, live_soup, ref_elements: dict, live_elements: dict) -> tuple[str, list]:
    """
    Aligns headings by content (tag, normalized_text) using sequence alignment,
    instead of comparing by index position. This means an inserted/deleted/reordered
    heading no longer cascades into false mismatches for every heading after it.
    """
    mismatches = []

    ref_h = ref_elements.get("headings", [])
    live_h = live_elements.get("headings", [])

    ref_keys = [(h["tag"], _normalize_heading_text(h["text"])) for h in ref_h]
    live_keys = [(h["tag"], _normalize_heading_text(h["text"])) for h in live_h]

    matcher = SequenceMatcher(a=ref_keys, b=live_keys, autojunk=False)

    for tag_op, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag_op == "equal":
            continue  # genuinely matched, no mismatch to report

        elif tag_op == "replace":
            # Same slot in the aligned sequence, different content -> modified heading
            span = min(i2 - i1, j2 - j1)
            for offset in range(span):
                r = ref_h[i1 + offset]
                l = live_h[j1 + offset]
                mismatches.append({
                    "type": "heading_modified",
                    "bbox": l["bbox"],
                    "message": f"{l['tag'].upper()} changed: expected '{r['text']}', found '{l['text']}'"
                })
            # leftover reference-side entries beyond the shorter span = deleted
            for offset in range(span, i2 - i1):
                r = ref_h[i1 + offset]
                mismatches.append({
                    "type": "missing_heading",
                    "bbox": r["bbox"],
                    "message": f"Missing {r['tag'].upper()}: '{r['text']}'"
                })
            # leftover live-side entries beyond the shorter span = inserted
            for offset in range(span, j2 - j1):
                l = live_h[j1 + offset]
                mismatches.append({
                    "type": "extra_heading",
                    "bbox": l["bbox"],
                    "message": f"Extra {l['tag'].upper()}: '{l['text']}'"
                })

        elif tag_op == "delete":
            for offset in range(i1, i2):
                r = ref_h[offset]
                mismatches.append({
                    "type": "missing_heading",
                    "bbox": r["bbox"],
                    "message": f"Missing {r['tag'].upper()}: '{r['text']}'"
                })

        elif tag_op == "insert":
            for offset in range(j1, j2):
                l = live_h[offset]
                mismatches.append({
                    "type": "extra_heading",
                    "bbox": l["bbox"],
                    "message": f"Extra {l['tag'].upper()}: '{l['text']}'"
                })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches


def _phash_hamming_distance(hash1, hash2):
    """Bit-difference between two perceptual hashes. None if either is missing/invalid."""
    if not hash1 or not hash2:
        return None
    try:
        int1 = int(hash1, 16)
        int2 = int(hash2, 16)
    except (ValueError, TypeError):
        return None
    return bin(int1 ^ int2).count("1")


def compare_images(ref_soup, live_soup, ref_elements: dict, live_elements: dict) -> tuple[str, list]:
    """
    Matches images primarily by exact src (fast, precise). For images that
    don't match by src, falls back to perceptual-hash similarity — this
    catches "same image, different URL" cases (CDN cache-busting, resize
    params, CMS path rewrites) and reports them as a distinct, lower-severity
    "cosmetic" mismatch instead of a false missing+extra pair.
    """
    PHASH_MATCH_THRESHOLD = 8  # lower = stricter match; tune against real data if noisy

    mismatches = []

    ref_imgs = ref_elements.get("images", [])
    live_imgs = live_elements.get("images", [])

    if len(ref_imgs) != len(live_imgs):
        mismatches.append({
            "type": "image_count",
            "ref_count": len(ref_imgs),
            "live_count": len(live_imgs),
            "message": f"Image count: expected {len(ref_imgs)}, found {len(live_imgs)}"
        })

    # --- Pass 1: match by exact src (unchanged from before) ---
    ref_dict = {img["src"]: img for img in ref_imgs if img["src"]}
    live_dict = {img["src"]: img for img in live_imgs if img["src"]}

    matched_srcs = set()
    for src, l_img in live_dict.items():
        r_img = ref_dict.get(src)
        if r_img:
            matched_srcs.add(src)
            if r_img["alt"] != l_img["alt"]:
                mismatches.append({
                    "type": "alt_mismatch",
                    "bbox": l_img["bbox"],
                    "message": f"Alt mismatch. Expected '{r_img['alt']}', found '{l_img['alt']}'"
                })

    ref_unmatched = [img for src, img in ref_dict.items() if src not in matched_srcs]
    live_unmatched = [img for src, img in live_dict.items() if src not in matched_srcs]

    # --- Pass 2: among src-unmatched images, try perceptual-hash similarity ---
    candidate_pairs = []
    for ri, r_img in enumerate(ref_unmatched):
        for li, l_img in enumerate(live_unmatched):
            dist = _phash_hamming_distance(r_img.get("phash"), l_img.get("phash"))
            if dist is not None and dist <= PHASH_MATCH_THRESHOLD:
                candidate_pairs.append((dist, ri, li))

    candidate_pairs.sort(key=lambda p: p[0])  # closest visual match first
    phash_matched_ref = set()
    phash_matched_live = set()

    for dist, ri, li in candidate_pairs:
        if ri in phash_matched_ref or li in phash_matched_live:
            continue
        phash_matched_ref.add(ri)
        phash_matched_live.add(li)
        r_img = ref_unmatched[ri]
        l_img = live_unmatched[li]
        mismatches.append({
            "type": "image_src_changed_cosmetic",
            "bbox": l_img["bbox"],
            "message": f"Image src changed but content appears identical (phash distance {dist}): '{r_img['src']}' -> '{l_img['src']}'"
        })
        if r_img["alt"] != l_img["alt"]:
            mismatches.append({
                "type": "alt_mismatch",
                "bbox": l_img["bbox"],
                "message": f"Alt mismatch. Expected '{r_img['alt']}', found '{l_img['alt']}'"
            })

    # --- Anything still unmatched after both passes = real missing/extra ---
    for li, l_img in enumerate(live_unmatched):
        if li not in phash_matched_live:
            mismatches.append({
                "type": "extra_image",
                "bbox": l_img["bbox"],
                "message": "Extra image in live"
            })

    for ri, r_img in enumerate(ref_unmatched):
        if ri not in phash_matched_ref:
            mismatches.append({
                "type": "missing_image",
                "bbox": r_img["bbox"],
                "message": f"Image missing in live (alt: '{r_img['alt']}')"
            })

    # Images with no alt at all in live (unchanged)
    for l_img in live_imgs:
        if not l_img["alt"].strip():
            mismatches.append({
                "type": "empty_alt",
                "bbox": l_img["bbox"],
                "message": "Missing alt attribute"
            })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches

def _normalize_btn_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _btn_text_similarity(a: str, b: str) -> float:
    """0.0-1.0 similarity between two normalized button texts."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def _bbox_center(bbox):
    if not bbox:
        return None
    return (bbox["x"] + bbox["width"] / 2, bbox["y"] + bbox["height"] / 2)


def _bbox_proximity_score(ref_bbox, live_bbox, max_bonus: float = 5.0, max_dist: float = 400.0) -> float:
    """Small tie-breaker bonus for buttons in roughly the same page region. Never primary signal."""
    rc = _bbox_center(ref_bbox)
    lc = _bbox_center(live_bbox)
    if not rc or not lc:
        return 0.0
    dist = ((rc[0] - lc[0]) ** 2 + (rc[1] - lc[1]) ** 2) ** 0.5
    if dist >= max_dist:
        return 0.0
    return max_bonus * (1 - dist / max_dist)


def _score_button_pair(ref_btn: dict, live_btn: dict) -> float:
    """
    Weighted score for how likely ref_btn and live_btn are the "same" button.
    Higher = more confident match. Signals are additive, not a cascade —
    a button can match on multiple weak signals even if no single one is exact.
    """
    score = 0.0

    ref_href = _normalize_href((ref_btn.get("href") or "").strip())
    live_href = _normalize_href((live_btn.get("href") or "").strip())
    if ref_href and live_href and ref_href == live_href:
        score += 50.0

    ref_text = _normalize_btn_text(ref_btn.get("text"))
    live_text = _normalize_btn_text(live_btn.get("text"))
    if ref_text and live_text:
        if ref_text == live_text:
            score += 30.0
        else:
            # Partial credit for near-identical text (e.g. "Buy Now" vs "Shop Now")
            score += 20.0 * _btn_text_similarity(ref_text, live_text)

    ref_aria = _normalize_btn_text(ref_btn.get("aria_label"))
    live_aria = _normalize_btn_text(live_btn.get("aria_label"))
    if ref_aria and live_aria and ref_aria == live_aria:
        score += 15.0

    score += _bbox_proximity_score(ref_btn.get("bbox"), live_btn.get("bbox"))

    return score

def _btn_signature(btn: dict) -> str:
    """
    Coarse bucket for buttons with no unique identity (icon-only nav toggles etc).
    Two buttons with the same signature are structurally interchangeable —
    compare them by count, not by trying to pair specific instances.
    """
    text = (btn.get("text") or "").strip()
    href = (btn.get("href") or "").strip()
    aria = (btn.get("aria_label") or "").strip()
    selector = btn.get("selector", "")
    return f"{selector}|text:{bool(text)}|href:{bool(href)}|aria:{aria.lower()}"

def compare_buttons(ref_elements: dict, live_elements: dict) -> tuple[str, list]:
    """
    Matches ref/live buttons via scored one-to-one assignment for buttons with
    a real identity (text/href/aria), and via count-per-bucket comparison for
    icon-only/generic buttons that repeat identically (e.g. nav dropdown chevrons)
    and therefore can't be meaningfully paired instance-to-instance.
    """
    mismatches = []

    ref_buttons = ref_elements.get("buttons", [])
    live_buttons = live_elements.get("buttons", [])

    def is_identifiable(btn):
        return bool(
            (btn.get("text") or "").strip() or
            (btn.get("href") or "").strip() or
            (btn.get("aria_label") or "").strip()
        )

    ref_identifiable_all = [b for b in ref_buttons if is_identifiable(b)]
    live_identifiable_all = [b for b in live_buttons if is_identifiable(b)]

    # --- Split off icon/generic buttons that repeat identically (2+ on a side) ---
    def bucket_counts(buttons):
        counts = {}
        for b in buttons:
            sig = _btn_signature(b)
            counts.setdefault(sig, []).append(b)
        return counts

    ref_buckets = bucket_counts(ref_identifiable_all)
    live_buckets = bucket_counts(live_identifiable_all)

    GENERIC_MIN_GROUP = 2  # 2+ identical buttons on a side = treat as a bucket, not individuals

    generic_sigs = {
        sig for sig, items in ref_buckets.items() if len(items) >= GENERIC_MIN_GROUP
    } | {
        sig for sig, items in live_buckets.items() if len(items) >= GENERIC_MIN_GROUP
    }

    ref_identifiable = [b for b in ref_identifiable_all if _btn_signature(b) not in generic_sigs]
    live_identifiable = [b for b in live_identifiable_all if _btn_signature(b) not in generic_sigs]

    # --- Bucket-level count comparison for generic/icon buttons ---
    for sig in sorted(generic_sigs):
        ref_n = len(ref_buckets.get(sig, []))
        live_n = len(live_buckets.get(sig, []))
        if ref_n != live_n:
            sample = (ref_buckets.get(sig) or live_buckets.get(sig))[0]
            mismatches.append({
                "type": "icon_button_count_mismatch",
                "bbox": sample.get("bbox"),
                "message": f"Icon/generic button group '{sig}': expected {ref_n}, found {live_n}"
            })

    # Count check — now only over the individually-identifiable set
    if len(ref_identifiable) != len(live_identifiable):
        mismatches.append({
            "type": "button_count",
            "ref_count": len(ref_identifiable),
            "live_count": len(live_identifiable),
            "message": f"Button count (identifiable): expected {len(ref_identifiable)}, found {len(live_identifiable)}"
        })

    # ---- Score every ref x live pair (unchanged) ----
    MIN_MATCH_SCORE = 10.0
    candidate_pairs = []
    for ri, ref_btn in enumerate(ref_identifiable):
        for li, live_btn in enumerate(live_identifiable):
            score = _score_button_pair(ref_btn, live_btn)
            if score >= MIN_MATCH_SCORE:
                candidate_pairs.append((score, ri, li))

    # ---- Greedy highest-score-first assignment (one-to-one) ----
    candidate_pairs.sort(key=lambda p: p[0], reverse=True)
    matched_ref = set()
    matched_live = set()
    assignments = []

    for score, ri, li in candidate_pairs:
        if ri in matched_ref or li in matched_live:
            continue
        matched_ref.add(ri)
        matched_live.add(li)
        assignments.append((ri, li, score))

    # ---- Report label/href mismatches for matched pairs ----
    for ri, li, score in assignments:
        ref_btn = ref_identifiable[ri]
        live_btn = live_identifiable[li]

        ref_text = (ref_btn.get("text") or "").strip()
        live_text = (live_btn.get("text") or "").strip()
        ref_aria = (ref_btn.get("aria_label") or "").strip()
        live_aria = (live_btn.get("aria_label") or "").strip()
        ref_href = _normalize_href((ref_btn.get("href") or "").strip())
        live_href = _normalize_href((live_btn.get("href") or "").strip())

        if ref_text.lower() != live_text.lower() or ref_aria.lower() != live_aria.lower():
            mismatches.append({
                "type": "button_label_mismatch",
                "bbox": live_btn.get("bbox"),
                "message": f"Button label mismatch: expected '{ref_text}', found '{live_text}'"
            })

        if ref_href != live_href:
            mismatches.append({
                "type": "button_href_mismatch",
                "bbox": live_btn.get("bbox"),
                "message": f"Button href changed: expected '{ref_href}', found '{live_href}'"
            })

    # ---- Unmatched ref buttons = missing ----
    for ri, ref_btn in enumerate(ref_identifiable):
        if ri not in matched_ref:
            mismatches.append({
                "type": "missing_button",
                "bbox": ref_btn.get("bbox"),
                "message": f"Missing button: '{ref_btn.get('text','').strip()}' (href: '{ref_btn.get('href','').strip()}')"
            })

    # ---- Unmatched live buttons = extra ----
    for li, live_btn in enumerate(live_identifiable):
        if li not in matched_live:
            mismatches.append({
                "type": "extra_button",
                "bbox": live_btn.get("bbox"),
                "message": f"Extra button in live: '{live_btn.get('text','').strip()}' (href: '{live_btn.get('href','').strip()}')"
            })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches

def compare_canonical(ref_soup, live_soup) -> tuple[str, list]:
    """
    Checks canonical tag presence and value match.
    """
    mismatches = []

    ref_tag = ref_soup.find("link", rel="canonical")
    live_tag = live_soup.find("link", rel="canonical")

    ref_href = ref_tag["href"].strip() if ref_tag and ref_tag.get("href") else None
    live_href = live_tag["href"].strip() if live_tag and live_tag.get("href") else None

    if ref_href and not live_href:
        mismatches.append({
            "type": "canonical_missing",
            "message": f"Canonical tag missing in live. Expected: {ref_href}"
        })
    elif ref_href and live_href and ref_href != live_href:
        mismatches.append({
            "type": "canonical_mismatch",
            "ref_value": ref_href,
            "live_value": live_href,
            "message": f"Canonical mismatch: ref='{ref_href}' live='{live_href}'"
        })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches


def compare_meta(ref_soup, live_soup) -> tuple[str, list]:
    """
    Compares page title, meta description, and meta keywords.
    """
    mismatches = []

    # Page title
    ref_title = ref_soup.find("title")
    live_title = live_soup.find("title")
    ref_title_text = ref_title.get_text(strip=True) if ref_title else ""
    live_title_text = live_title.get_text(strip=True) if live_title else ""

    if ref_title_text != live_title_text:
        mismatches.append({
            "type": "title_mismatch",
            "ref_value": ref_title_text,
            "live_value": live_title_text,
            "message": "Page title mismatch"
        })

    # Meta description + keywords
    for name in ["description", "keywords"]:
        ref_tag = ref_soup.find("meta", attrs={"name": name})
        live_tag = live_soup.find("meta", attrs={"name": name})
        ref_val = ref_tag.get("content", "").strip() if ref_tag else ""
        live_val = live_tag.get("content", "").strip() if live_tag else ""

        if ref_val != live_val:
            mismatches.append({
                "type": f"meta_{name}_mismatch",
                "ref_value": ref_val,
                "live_value": live_val,
                "message": f"Meta {name} mismatch"
            })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches


def compare_og_tags(ref_soup, live_soup) -> tuple[str, list]:
    """
    Compares og:title, og:description, og:keywords.
    """
    mismatches = []

    for prop in ["og:title", "og:description", "og:keywords"]:
        ref_tag = ref_soup.find("meta", property=prop)
        live_tag = live_soup.find("meta", property=prop)
        ref_val = ref_tag.get("content", "").strip() if ref_tag else ""
        live_val = live_tag.get("content", "").strip() if live_tag else ""

        if ref_val != live_val:
            mismatches.append({
                "type": f"og_tag_mismatch",
                "property": prop,
                "ref_value": ref_val,
                "live_value": live_val,
                "message": f"{prop} mismatch"
            })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches


def compare_links(ref_soup, live_soup, ref_elements: dict, live_elements: dict) -> tuple[str, list]:
    from collections import Counter
    mismatches = []

    ref_links = ref_elements.get("links", [])
    live_links = live_elements.get("links", [])

    # Extract base URLs from canonical tags so relative hrefs resolve correctly
    ref_canonical = ref_soup.find("link", rel="canonical")
    live_canonical = live_soup.find("link", rel="canonical")
    ref_base = ref_canonical["href"].strip() if ref_canonical and ref_canonical.get("href") else ""
    live_base = live_canonical["href"].strip() if live_canonical and live_canonical.get("href") else ""

    # Count check
    if len(ref_links) != len(live_links):
        mismatches.append({
            "type": "link_count",
            "ref_count": len(ref_links),
            "live_count": len(live_links),
            "message": f"Link count: expected {len(ref_links)}, found {len(live_links)}"
        })

    # Build normalized lists, resolving relative URLs against their respective base
    ref_bbox_map = {}
    live_bbox_map = {}

    ref_normalized = []
    for l in ref_links:
        n = _normalize_href(l.get("href", ""), base_url=ref_base)
        ref_normalized.append(n)
        if n not in ref_bbox_map:
            ref_bbox_map[n] = l.get("bbox")

    live_normalized = []
    for l in live_links:
        n = _normalize_href(l.get("href", ""), base_url=live_base)
        live_normalized.append(n)
        if n not in live_bbox_map:
            live_bbox_map[n] = l.get("bbox")

    ref_counter = Counter(ref_normalized)
    live_counter = Counter(live_normalized)

    for href, ref_n in ref_counter.items():
        live_n = live_counter.get(href, 0)
        if live_n < ref_n:
            for _ in range(ref_n - live_n):
                mismatches.append({
                    "type": "missing_link",
                    "bbox": ref_bbox_map.get(href),
                    "message": f"Missing link ({ref_n - live_n}x): {href}"
                })

    for href, live_n in live_counter.items():
        ref_n = ref_counter.get(href, 0)
        if live_n > ref_n:
            for _ in range(live_n - ref_n):
                mismatches.append({
                    "type": "extra_link",
                    "bbox": live_bbox_map.get(href),
                    "message": f"Extra link ({live_n - ref_n}x): {href}"
                })

    status = "PASS" if not mismatches else "FAIL"
    return status, mismatches
# -------------------------------------------------------------------
# Annotation runner
# -------------------------------------------------------------------

def annotate_screenshot(device: str, slug: str, report: dict, show_all: bool = False):
    """
    Draws bounding boxes and labels on the live screenshot based on the report.
    Saves to the 'diffs/' folder.
    """
    live_img_path = os.path.join(DATA_DIR, "live", f"{device}-{slug}", f"live-{device}-{slug}-screenshot.png")
    if not os.path.exists(live_img_path):
        print(f"  [Annotate] Live screenshot not found: {live_img_path}")
        return

    try:
        img = Image.open(live_img_path)
    except Exception as e:
        print(f"  [Annotate] Failed to open image {live_img_path}: {e}")
        return

    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=16)
    except Exception:
        font = ImageFont.load_default()

    details = report.get("details", {})
    floating_messages = []

    for category, issues in details.items():
        if not isinstance(issues, list):
            continue
        for issue in issues:
            bbox = issue.get("bbox")
            label = issue.get("message", "Mismatch")

            if bbox is None:
                floating_messages.append(label)
                continue

            x = bbox["x"]
            y = bbox["y"]
            w = bbox["width"]
            h = bbox["height"]
            
            # Draw red rectangle
            draw.rectangle([(x, y), (x + w, y + h)], outline="red", width=3)
            
            # Truncate label if too long
            if len(label) > 60:
                label = label[:57] + "..."
            
            text_y = max(0, y - 20)
            try:
                text_bbox = draw.textbbox((x, text_y), label, font=font)
                label_w = text_bbox[2] - text_bbox[0]
                
                # Shift X if it exceeds image width
                if x + label_w > img.width:
                    x = max(0, img.width - label_w)
                    text_bbox = draw.textbbox((x, text_y), label, font=font)
                
                draw.rectangle(text_bbox, fill="red")
            except AttributeError:
                pass # Fallback for very old Pillow versions that lack textbbox
            
            draw.text((x, text_y), label, fill="white", font=font)

    # Save floating messages and SEO status to a text file
    warnings_path = os.path.join(DATA_DIR, "diffs", f"{device}-{slug}-non-visual-warnings.txt")
    with open(warnings_path, "w", encoding="utf-8") as f:
        f.write(f"Non-Visual / SEO Status for {device} ({slug})\n")
        f.write("="*50 + "\n\n")
        
        # Print SEO Statuses
        summary = report.get("summary", {})
        f.write("[SEO Status Overview]\n")
        f.write(f"- Canonical Tags: {summary.get('canonical', 'N/A')}\n")
        f.write(f"- Meta Tags:      {summary.get('meta', 'N/A')}\n")
        f.write(f"- Open Graph:     {summary.get('og_tags', 'N/A')}\n\n")

        f.write("[Specific Non-Visual Mismatches]\n")
        if floating_messages:
            for msg in floating_messages:
                f.write(f"- {msg}\n")
        else:
            f.write("- All correct! No non-visual mismatches found.\n")
            
    print(f"  Non-visual warnings saved to {warnings_path}")

    os.makedirs(os.path.join(DATA_DIR, "diffs"), exist_ok=True)
    out_path = os.path.join(DATA_DIR, "diffs", f"{device}-{slug}-annotated.png")
    img.save(out_path)
    print(f"  Annotated screenshot saved to {out_path}")

# -------------------------------------------------------------------
# Main comparison runner for one device
# -------------------------------------------------------------------

def compare_device(device: str, slug: str) -> dict:
    """
    Runs all comparators for a single device viewport.
    Returns a result dict with statuses + all mismatch details.
    """
    print(f"\n[{device}] Comparing...")

    ref_soup = load_html("reference", device, slug)
    live_soup = load_html("live", device, slug)
    ref_elements = load_elements("reference", device, slug)
    live_elements = load_elements("live", device, slug)

    # Run all comparators
    heading_status,  heading_issues  = compare_headings(ref_soup, live_soup, ref_elements, live_elements)
    image_status, image_issues = "SKIPPED", []  # images comparator disabled for now — phash threshold needs recalibration
    button_status,   button_issues   = compare_buttons(ref_elements, live_elements)
    canonical_status,canonical_issues= compare_canonical(ref_soup, live_soup)
    meta_status,     meta_issues     = compare_meta(ref_soup, live_soup)
    og_status,       og_issues       = compare_og_tags(ref_soup, live_soup)
    link_status,     link_issues     = compare_links(ref_soup, live_soup, ref_elements, live_elements)
    
    visual_status, visual_issues = compare_visual_folds(
        reference_dir=os.path.join(DATA_DIR, "reference", f"{device}-{slug}"),
        live_dir=os.path.join(DATA_DIR, "live", f"{device}-{slug}"),
        out_dir=os.path.join(DATA_DIR, "diffs", "folds", f"{device}-{slug}"),
        device=device,
        slug=slug,
        target_fold_height=2500,
        repo_root=os.path.dirname(DATA_DIR),
    )
    # Print a quick summary to terminal
    results = {
        "headings":  (heading_status,   heading_issues),
        "images":    (image_status,     image_issues),
        "buttons":   (button_status,    button_issues),
        "canonical": (canonical_status, canonical_issues),
        "meta":      (meta_status,      meta_issues),
        "og_tags":   (og_status,        og_issues),
        "links":     (link_status,      link_issues),
        "visual_folds": (visual_status, visual_issues),
    }

    for category, (status, issues) in results.items():
        icon = "✅" if status == "PASS" else "❌"
        print(f"  {icon} {category}: {status}", end="")
        if issues:
            print(f" ({len(issues)} issue(s))")
        else:
            print()

    # Build the report dict for this device
    report = {
        "device": device,
        "slug": slug,
        "summary": {
            "headings":  heading_status,
            "images":    image_status,
            "buttons":   button_status,
            "canonical": canonical_status,
            "meta":      meta_status,
            "og_tags":   og_status,
            "links":     link_status,
            "visual_folds": visual_status,
        },
        "details": {
            "headings":  heading_issues,
            "images":    image_issues,
            "buttons":   button_issues,
            "canonical": canonical_issues,
            "meta":      meta_issues,
            "og_tags":   og_issues,
            "links":     link_issues,
            "visual_folds": visual_issues,
        }
    }

    return report

def generate_summary_report(all_reports: list, slug: str):
    os.makedirs(os.path.join(DATA_DIR, "diffs"), exist_ok=True)
    path = os.path.join(DATA_DIR, "diffs", f"{slug}-problems.txt")

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"BUTTON DIFF REPORT — {slug}\n")
        f.write("=" * 60 + "\n\n")

        for report in all_reports:
            device = report["device"]
            details = report["details"]
            btn_issues = details.get("buttons", [])

            missing = [i for i in btn_issues if i.get("type") == "missing_button"]
            extra = [i for i in btn_issues if i.get("type") == "extra_button"]

            f.write(f"[ {device.upper()} ]\n")
            f.write("-" * 40 + "\n")

            # Count line
            ref_count = next((i.get("ref_count") for i in btn_issues if i.get("type") == "button_count"), None)
            live_count = next((i.get("live_count") for i in btn_issues if i.get("type") == "button_count"), None)
            if ref_count is not None:
                f.write(f"Reference identifiable buttons: {ref_count}\n")
                f.write(f"Live identifiable buttons:      {live_count}\n\n")

            f.write(f"REF only (missing from live) ({len(missing)}):\n")
            for i in missing:
                text = i.get("message", "").replace("Missing button: ", "").replace("Missing button:", "")
                f.write(f"  - {text}\n")

            f.write(f"\nLIVE only (extra) ({len(extra)}):\n")
            for i in extra:
                text = i.get("message", "").replace("Extra button in live: ", "").replace("Extra button in live:", "")
                f.write(f"  - {text}\n")

            f.write("\n")

    print(f"Button diff report saved to {path}")
# -------------------------------------------------------------------
# CLI entry point
# -------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compare reference vs live HTML structure across all viewports."
    )
    parser.add_argument(
        "--slug",
        required=True,
        help="Page slug used during capture. e.g. rd-calculator"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Annotate all elements, not just mismatches"
    )
    args = parser.parse_args()

    all_reports = []
    devices = ["desktop", "android", "ios"]

    for device in devices:
        try:
            report = compare_device(device, args.slug)
            all_reports.append(report)
            annotate_screenshot(device, args.slug, report, show_all=args.all)
        except FileNotFoundError as e:
            print(f"\n[{device}] Skipping — {e}")

    # Save combined report to reports/
    os.makedirs(os.path.join(DATA_DIR, "reports"), exist_ok=True)
    report_path = os.path.join(DATA_DIR, "reports", f"{args.slug}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(all_reports, f, indent=2)

    print(f"\nReport saved to {report_path}")
    generate_summary_report(all_reports, args.slug)

