# Deep Dive — Technical Architecture

This document explains the internal algorithms, data flow, and design decisions behind every component of `playwright-compare`. Read [DOCUMENTATION.md](./DOCUMENTATION.md) first for setup and usage.

---

## Table of Contents

1. [Core Pipeline Overview](#1-core-pipeline-overview)
2. [Capture — `core/capture.py`](#2-capture)
3. [Compare — `core/compare.py`](#3-compare)
4. [Fold Diffing — `core/fold_utils.py`](#4-fold-diffing)
5. [Backend API — `backend/`](#5-backend-api)
6. [Sticky Feature — `features/sticky/`](#6-sticky-feature)
7. [Design Decisions & Fixes](#7-design-decisions--fixes)

---

## 1. Core Pipeline Overview

The CLI pipeline has three stages, each a separate Python script:

```
capture.py (reference)  ──┐
                          ├──►  compare.py  ──►  reports + annotated screenshots
capture.py (live)       ──┘
```

All paths are anchored to `__file__` via `BASE_DIR` / `DATA_DIR`, so the scripts work from any working directory.

---

## 2. Capture — `core/capture.py`

### 2.1 Viewport Configurations

Three viewports are captured per URL:

| Viewport | Device | Width x Height | Scale Factor |
|----------|--------|----------------|--------------|
| Desktop | Custom | 1280 x 800 | 1.0 |
| Android | Pixel 5 | Playwright built-in | 2.75 |
| iOS | iPhone 13 Mini | Playwright built-in | 3.0 |

Each viewport runs in a separate Chromium instance (launched and closed sequentially).

### 2.2 Page Load Strategy

1. **Navigate** — `page.goto(url, wait_until="domcontentloaded", timeout=45s)` with one retry on timeout or network error (`_goto_with_retry`).
2. **Wait for body** — `page.wait_for_selector("body", timeout=15s)` instead of `networkidle`, which can hang forever on pages with persistent background network activity (analytics pings, WebSocket heartbeats).
3. **Initial settle** — 2-second static wait for JS frameworks to hydrate.
4. **Popup dismissal** — `_dismiss_popup()` tries up to 11 CSS selectors (`.new-investment-popup-close`, `[aria-label*='close' i]`, `.close`, etc.) with a 250ms step loop. If no popup/modal container classes exist in the DOM, the wait is capped at 1 second instead of the full 3 seconds.
5. **Lazy-load scroll** — `_scroll_full_page()` step-scrolls in 400px increments (100ms interval) to trigger `loading="lazy"` images and intersection-observer-based loaders. A single jump wouldn't trigger these. Hard-capped at 30,000px total distance and 150 iterations to prevent infinite-scroll pages from growing unbounded.
6. **Second popup dismissal** — catches popups triggered by scroll.
7. **Image load wait** — `_wait_images_loaded()` polls `Array.from(document.images).every(img => img.complete)` with a 3-second timeout.

### 2.3 Full-Page Screenshot — Chunked Stitching

**Problem:** Chromium has a texture size rendering limit (~16,384 physical pixels). Pages taller than this (common on mobile with high DPR — e.g. 3000 CSS px × 3.0 DPR = 9000 physical px, but real pages can be 60,000+ CSS px) either fail or produce tiled repetition bugs.

**Solution — `_screenshot_full_page_stable()`:**

1. **Short page path:** If the page is ≤ 2× viewport height AND fits within 14,000 physical px, resize the viewport to the full page height and take a single screenshot. This avoids layout reflow bugs from extreme viewport resizing.

2. **Chunked path (tall pages):**
   - Keep the viewport at its original size (no resizing — preserves real-world layout).
   - Scroll to each chunk position, hide sticky/fixed elements via a CSS stylesheet injection, take a viewport screenshot, and collect the PIL Image.
   - For the last chunk, crop the bottom to the exact remaining height.
   - Stitch all chunks vertically into a single RGBA image.
   - Restore the original viewport and scroll position.

**Sticky element hiding:** Uses a `<style>` tag injected into `<head>` with `[data-_capturehide="1"] { visibility: hidden !important; }`. This is preferred over inline styles because React re-renders on scroll events can overwrite inline styles but cannot remove a `<style>` tag. `visibility: hidden` is used (not `display: none` or `position: static`) to preserve layout — `position: static` caused a blue-overlay artifact where the nav flowed into the content area.

**Which elements are hidden:**
- First chunk (scroll position 0): Only bottom-pinned sticky banners (CTA bars).
- Subsequent chunks: All fixed/sticky elements (top nav + bottom banners).
- Detection: Elements with `position: fixed/sticky` whose bounding box overlaps the top 120px or bottom 120px of the viewport, and are wider than 50% of viewport width.

### 2.4 Element Extraction — `extract_elements(page)`

Walks the live DOM via Playwright and records structured data with bounding boxes for:

| Category | Selector | Data Captured |
|----------|----------|---------------|
| Headings | `h1`–`h6` | tag, text (truncated to 80 chars), bbox |
| Images | `img` | alt, src, bbox (phash currently `None`) |
| Buttons | `button, input[type='button'], input[type='submit'], [role='button']` | text, href (with ancestor/descendant `<a>` lookup), aria_label, selector kind, bbox |
| Links | `a` | href, bbox |
| Canonical | `link[rel='canonical']` | href (no bbox — lives in `<head>`) |
| Meta | `title`, `meta[name='description']`, `meta[name='keywords']` | name, value (no bbox) |
| OG Tags | `meta[property='og:title']`, `og:description`, `og:keywords` | property, value (no bbox) |

Bounding boxes are scaled by `device_scale_factor` so they map to physical pixel coordinates in the screenshot image.

### 2.5 Button Deduplication — `_dedupe_buttons()`

Two DOM elements can both match button selectors for what is really one clickable control (e.g. `<div role="button"><button>...</button></div>`). The dedup:

1. For each button, check if its bounding box overlaps >90% with any already-kept button (overlap ratio = intersection area / min(area_a, area_b)).
2. If overlap >90%, keep the one with a higher **identity score** (non-empty text +1, non-empty href +1, non-empty aria-label +1).
3. Tie-break: keep the smaller (innermost) element.

---

## 3. Compare — `core/compare.py`

### 3.1 Heading Comparator — `compare_headings()`

Uses **sequence alignment** (`difflib.SequenceMatcher`) on `(tag, normalized_text)` tuples rather than index-based comparison. This means an inserted, deleted, or reordered heading doesn't cascade false mismatches for everything after it.

SequenceMatcher produces opcodes (`equal`, `replace`, `delete`, `insert`):
- `replace` → `heading_modified` (content changed in same slot)
- `delete` → `missing_heading` (present in reference, absent in live)
- `insert` → `extra_heading` (present in live, absent in reference)

Text normalization: lowercase + collapse whitespace.

### 3.2 Button Comparator — `compare_buttons()`

Two-phase approach:

**Phase 1: Bucket-level comparison for generic/icon buttons.**
- Buttons with a recognizable identity (text, href, or aria-label) are considered "identifiable."
- Among identifiable buttons, those with identical signatures (same selector kind, same has-text/has-href/has-aria pattern) that appear 2+ times on either side are bucketed together.
- Bucket counts are compared directly — if reference has 3 nav chevrons and live has 2, that's a mismatch.

**Phase 2: Scored one-to-one assignment for identifiable buttons.**
- Every ref × live pair gets a **match score** (0–100):
  - Exact href match: +50
  - Exact text match: +30, partial text similarity: +20 × `SequenceMatcher.ratio()`
  - Exact aria-label match: +15
  - Bbox proximity bonus: up to +5 (linear falloff over 400px)
- Minimum match score: 10.0
- Greedy highest-score-first assignment (one-to-one — each button matched at most once).
- Matched pairs: report label and href mismatches.
- Unmatched reference buttons → `missing_button`.
- Unmatched live buttons → `extra_button`.

### 3.3 Link Comparator — `compare_links()`

1. **Count check** — simple ref vs live link count.
2. **Normalization** — internal domains (`axismaxlife.com`, `neouat.axismaxlife.com`) are stripped to path-only for comparison (prod vs UAT domain differences don't register). External links keep their full URL.
3. **Group by href** — identical-href instances (e.g. 4× "...SIP" links) are interchangeable; matched by count, not by position.
4. **Leftover pairing** — remaining unmatched ref/live links are paired by bbox proximity (≤200px) to catch "same slot, destination changed" cases.
5. **Truly unmatched** → `missing_link` / `extra_link`.

### 3.4 Image Comparator — `compare_images()` (disabled)

Two-pass matching:
1. **Exact src match** — compare alt text for matched images.
2. **Perceptual hash** — for unmatched images, compute hamming distance between phash values. Distance ≤ 8 → cosmetic src change (CDN rewrite). Above → missing/extra.

Currently disabled because:
- Phash computation was removed from capture (too slow per-image).
- Empty-alt check had a bug: it flagged every `alt=""` image, not just regressions. Fixed to only flag if the reference had a non-empty alt.

### 3.5 Meta / OG / Canonical Comparators

Straightforward exact/normalized comparisons:
- `compare_canonical()` — checks presence + href value.
- `compare_meta()` — title text, description, keywords.
- `compare_og_tags()` — og:title, og:description, og:keywords.

### 3.6 Annotation Renderer — `annotate_screenshot()`

Draws all mismatches with a `bbox` onto the live screenshot:
- Red rectangle outline (thickness scaled by device scale factor).
- Red label background + white text above the box (truncated to 60 chars).
- Labels without bboxes (SEO mismatches) are skipped visually (saved to a separate warnings text file).
- Output: `data/diffs/{slug}/{device}-annotated.png`.

### 3.7 Report Generation

- **`raw-report.json`** — full structured data, all devices, all categories.
- **`reports/{slug}.json`** — same data, also consumed by server.py and the frontend.
- **`report.md`** — human-readable Markdown with a summary table and per-device issue lists.
- **`manifest.json`** — auto-generated index of all slugs with their available devices and report status.

---

## 4. Fold Diffing — `core/fold_utils.py`

### 4.1 Why It Exists

Structural comparators (headings, buttons, links) only catch what they're explicitly built to check. Fold-based visual diffing catches layout/positioning drift, image content changes, and general visual noise — at the cost of not being able to say *what* changed.

### 4.2 Fold Splitting — `split_into_folds()`

1. Load the full-page screenshot and `elements.json`.
2. Collect every element's bbox bottom edge (`y + height`) from all categories.
3. Choose cut points near every multiple of `target_fold_height` (2500px), snapped to the nearest element bottom within a 150px window. This ensures cuts never land in the middle of an element.
4. Crop the screenshot at these cut points. Discard folds shorter than 40px.
5. Save each fold as `{mode}-fold{N}.png`.

**Path split:** Each fold gets two paths:
- `path` — absolute filesystem path (used internally for `Image.open()`)
- `web_path` — repo-root-relative path (written to JSON, used by the frontend for HTTP serving)

### 4.3 Pixel Diff — `compute_fold_diff()`

1. Open reference and live fold as RGB PIL Images.
2. If sizes differ, naive resize to match (v1 placeholder).
3. `ImageChops.difference()` → per-pixel absolute difference.
4. Threshold at 25 (noise floor) → binary mask.
5. Count changed pixels / total pixels → diff percentage.

### 4.4 Orchestration — `compare_visual_folds()`

1. Split both reference and live screenshots into folds.
2. Pair by index (fold 0 ↔ fold 0, fold 1 ↔ fold 1, ...).
3. Compute diff score for each pair.
4. **All folds are returned** (not just flagged ones) with a `flagged: true/false` field (threshold: 5% diff). This is critical — the original implementation filtered to only flagged folds, which made fold 0 disappear from the JSON, causing a frontend bug where folds appeared to start at index 1 or 2.

---

## 5. Backend API — `backend/`

There are two backend implementations:

### 5.1 `server.py` (Simple)

A minimal FastAPI app using a background thread + queue. Runs `capture.py` → `capture.py` → `compare.py` as subprocesses. Serves the legacy `compare.html` frontend and the `data/` directory as static files.

### 5.2 `backend/` (Full App)

A production-oriented backend with parallel crawling, structured diff engine, caching, and on-demand annotation.

#### Crawler Engine — `crawler_engine.py`

Spawns **6 parallel threads** (3 viewports × 2 modes), each with its own Playwright browser instance:

```
desktop × reference
desktop × live
ios × reference
ios × live
android × reference
android × live
```

Each thread captures 4 sections per viewport:

1. **Fullpage** — full-page screenshot + elements JSON.
2. **First Fold** — viewport-only screenshot (no scroll), elements filtered to those with `y < viewport_height`.
3. **Sticky** — scrolled to 50% page height, viewport screenshot, sticky-only elements extracted via `isStickyOrFixed()` DOM walk.
4. **Popup** — auto-detects modals via 5 trigger strategies (see below), screenshots the popup region.

#### Popup Detection Strategies

1. **Auto-appear** — wait up to 5s for a modal to appear on its own.
2. **Scroll trigger** — scroll down 500px, back to top, scan again.
3. **Tab visibility switch** — fake `visibilitychange` to `hidden` then `visible`.
4. **Exit-intent mouse** — move mouse to top of viewport.
5. **Longer passive wait** — 10 more seconds of scanning.

Modal detection criteria: element must be ≥200×150px, cover ≥5% of viewport, and have at least one "modal trait" (close button, form inputs, backdrop overlay, high z-index).

#### Diff Engine — `diff_engine.py`

**Element alignment** (`match_elements()`) uses a 5-strategy cascade:

1. **By ID** — exact `id` attribute match (same tag + same id).
2. **By name** — exact `name` attribute match.
3. **By stable attributes** — tag-specific: img `src` (normalized), link `href` (normalized), heading `text` (>3 chars).
4. **By CSS selector path** — exact `selector` string match (fallback).
5. **By sequential order** — first-unmatched of same tag type (last resort).

**Diff logic** (`diff_elements()`):
- Unmatched reference → `missing` issue.
- Unmatched live → `extra` issue.
- Matched pairs → attribute-specific diffs (text content, tag level, font family, alt text, src, href, dimensions).

**SEO diff** (`diff_seo()`): Compares title, description, keywords, canonical, OG tags, Twitter Card tags, and hreflangs.

**Font stack check**: Compares `body` computed font-family across reference and live.

**Popup presence check**: If one side has a popup and the other doesn't, that's flagged.

**Sticky count check**: If the number of sticky elements differs between reference and live, that's flagged.

#### Cache — `cache.py`

In-memory TTL cache (5 minutes). Hashes `ref_url + live_url` → `run_id`. If a cached run exists and didn't fail, it's reused.

#### Exporter — `exporter.py`

On-demand annotation: reads `live.png` and `diff.json` for a given viewport/section, draws red bounding boxes and labels using PIL, saves as `live-annotated.png`, returns the static URL path.

---

## 6. Sticky Feature — `features/sticky/`

### 6.1 Capture (`capture-sticky.py`)

Captures sticky/fixed elements at multiple scroll positions (10%–100% of page height in 10% increments). For each scroll position:
- Scrolls to that position.
- Takes a viewport screenshot.
- Extracts elements (headings, images, buttons, links, sticky) visible at that scroll position.
- Saves per-scroll-position element JSON.

### 6.2 Compare (`compare-sticky.py`)

Runs the standard comparators (headings, buttons, links, images, canonical, meta, OG tags) plus a **sticky-specific comparator**:

- **Presence check** — is there a sticky element on one side but not the other?
- **Count check** — do the counts match?
- **Detailed element comparison** — normalizes each sticky element to `(tag, rounded_x, rounded_y, rounded_width, rounded_height)` and computes set differences (missing vs extra).

**Annotation**: Draws bounding boxes only onto screenshots where the flagged element was actually present at that scroll position (checked by matching bbox coordinates against the element JSON at that scroll %). Screenshots with no relevant issues are skipped entirely.

---

## 7. Design Decisions & Fixes

- **Path hardening**: All scripts resolve `data/` via `BASE_DIR`/`DATA_DIR` anchored to `__file__`, not the working directory.
- **Absolute vs. relative path split for fold images**: `split_into_folds()` keeps absolute paths internally (for `Image.open()`) and separately computes repo-root-relative paths for JSON/frontend consumption. Passing only one or the other broke either Python-side diffing or browser-side rendering.
- **All folds returned, not just flagged**: The original `compare_visual_folds` filtered to only folds above the 5% threshold, silently dropping fold 0 and making the frontend appear to start at index 1 or 2.
- **Nested fold storage**: Fold images originally dumped flat into `data/diffs/`. Now nested under `data/diffs/folds/{device}-{slug}/` with simplified filenames (the folder name already carries the context).
- **Lazy-loaded images**: Originally `capture.py` went straight from `networkidle` to `screenshot()` with no scrolling. Below-the-fold lazy images rendered as blank. Fixed by step-scrolling in 400px increments (intersection-observer-based lazy loaders need the image to cross the viewport boundary).
- **`img-comparison-slider` styling**: Requires both `<script>` and `<link rel="stylesheet">` — missing the stylesheet leaves the handle invisible. Only 3 CSS custom properties are supported in v8; reliable handle styling requires a custom `slot="handle"` element.
- **`visibility: hidden` over `position: static` for sticky hiding**: Using `position: static` to hide nav elements caused a blue-overlay artifact where the nav flowed into the content area.
