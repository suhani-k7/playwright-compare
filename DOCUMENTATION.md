# playwright-compare

A visual regression testing tool that captures screenshots and element data from a **reference** (production) and **live** (staging/UAT) version of a webpage, then runs automated comparisons across three viewports (Desktop, Android, iOS) to detect visual, structural, and content regressions.

---

## Prerequisites

- **Python 3.10+**
- **Google Chrome / Chromium** (installed automatically by Playwright)

---

## Installation

```bash
# 1. Clone the repo
git clone <repo-url>
cd playwright-compare

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate         # Windows

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install Playwright browsers
playwright install chromium
```

---

## How to Run

There are three ways to use this project, depending on your needs.

### Option A: CLI Pipeline

The simplest path — run capture and compare from the terminal.

```bash
# Step 1: Capture the reference (production) version
python3 core/capture.py --url "https://production.example.com/page" --mode reference --slug my-page

# Step 2: Capture the live (staging) version
python3 core/capture.py --url "https://staging.example.com/page" --mode live --slug my-page

# Step 3: Compare both captures
python3 core/compare.py --slug my-page
```

**Arguments:**

| Flag | Required | Description |
|------|----------|-------------|
| `--url` | Yes | Full URL to capture |
| `--mode` | Yes | `reference` or `live` |
| `--slug` | Yes | Short identifier for the page (e.g. `rd-calculator`, `home-page`) |

**What happens:** Each capture opens the URL in Chromium across 3 viewports (Desktop 1280x800, Pixel 5, iPhone 13 Mini), takes full-page screenshots, saves rendered HTML, and extracts element positions (headings, images, buttons, links, meta tags). The compare step loads both sets and runs all comparators.

### Option B: Vanilla Landing Page (server.py)

A self-contained HTML+API server that runs the CLI pipeline (`capture.py` → `compare.py`) behind a simple web UI. No React frontend required.

```bash
python3 server.py
```

Then open **http://localhost:8000** in your browser.

- Enter a reference URL — the live URL and slug are auto-derived
- Click **Compare Sites** — runs capture + compare in a background thread
- Progress is polled and displayed in the UI
- On completion, the fold slider viewer opens automatically in a new tab
- If a slug already exists, results open immediately without re-capturing

> **Note:** `server.py` runs captures sequentially (not in parallel) and uses the same `core/capture.py` and `core/compare.py` as the CLI pipeline.

### Option C: Fold Slider Viewer (Standalone)

A standalone HTML page for viewing pixel-level fold diffs via the CLI pipeline output.

```bash
# First, capture and compare via CLI (see Option A), then:
python3 -m http.server 8000
```

Then open:
```
http://localhost:8000/frontend/fold_slider_demo.html?slug=my-page&device=desktop
```

This is a static page that reads from `data/` — no backend server needed, but you must have run the CLI pipeline first.

---

## Project Structure

```
playwright-compare/
├── core/                        # CLI pipeline modules
│   ├── capture.py               # Captures screenshots, HTML, element data
│   ├── compare.py               # Runs all comparators, generates reports
│   └── fold_utils.py            # Fold-based visual pixel diffing
├── server.py                    # Vanilla landing page server (runs CLI pipeline as subprocesses)
├── frontend/                    # Static HTML pages (no build step)
│   ├── landing.html             # Main dashboard — launch comparisons, view history
│   ├── results.html             # Side-by-side results viewer with zoom + report drawer
│   ├── fold_slider_demo.html    # Fold-by-fold visual comparison slider
│   └── compare.html             # Legacy simple comparison UI
├── features/                    # Feature-specific modules
│   ├── sticky/                  # Sticky/fixed element comparison
│   │   ├── capture-sticky.py
│   │   ├── compare-sticky.py
│   │   ├── reference/
│   │   ├── diffs/
│   │   └── reports/
│   └── popup/                   # Popup detection
│       ├── capture-popup.py
│       └── compare-popup.py
├── data/                        # Pipeline output directory
│   ├── reference/               # Reference captures (per slug/device)
│   ├── live/                    # Live captures (per slug/device)
│   ├── diffs/                   # Comparison outputs (annotated PNGs, warnings)
│   ├── reports/                 # Structured JSON reports (per slug)
│   └── manifest.json            # Auto-generated index of all slugs
├── requirements.txt
├── DOCUMENTATION.md
└── DEEP_DIVE.md
```

---

## Output Formats

### CLI Pipeline Outputs

After running `compare.py --slug my-page`, you get:

**Per-device screenshots and data** (`data/{mode}/{slug}/{device}/`):
```
data/reference/rd-calculator/desktop/
├── reference-desktop-rd-calculator-screenshot.png
├── reference-desktop-rd-calculator-page.html
└── reference-desktop-rd-calculator-elements.json
```

**Comparison outputs** (`data/diffs/{slug}/`):
```
data/diffs/rd-calculator/
├── desktop-annotated.png         # Live screenshot with red boxes on issues
├── report.md                     # Human-readable Markdown report (all devices)
├── raw-report.json               # Machine-readable JSON (all devices)
└── folds/
    └── desktop/
        ├── reference-fold0.png   # Fold image pairs for visual diffing
        ├── live-fold0.png
        ├── reference-fold1.png
        └── ...
```

**Report JSON** (`data/reports/{slug}.json`): Structured per-device, per-category results (status + mismatch details). Used by server.py and the frontend.

---

## API Endpoints (server.py)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/compare` | Start a comparison. Body: `{reference_url, live_url, slug, force_refresh}`. Returns `{status, job_id, slug}`. |
| `GET` | `/api/jobs/{job_id}` | Poll job status: `queued` → `running` → `done` / `error`. Returns `{status, progress, error}`. |
| `GET` | `/api/reports/{slug}` | Get comparison report JSON for a slug. |
| `GET` | `/` | Serves the vanilla landing page (`landing.html`). |
| `GET` | `/results` | Serves the side-by-side results viewer (`results.html`). |

---

## Comparators

The CLI pipeline runs these comparators per device:

| Comparator | What it checks | Status |
|------------|----------------|--------|
| Headings | Content + level changes (sequence-aligned) | Active |
| Buttons | Label/href changes, missing/extra buttons | Active |
| Links | href changes, missing/extra links | Active |
| Canonical | Canonical tag presence + value | Active |
| Meta Tags | Title, description, keywords | Active |
| OG Tags | og:title, og:description, og:keywords | Active |
| Images | Alt text, src changes, missing/extra | **Disabled** (phash threshold needs recalibration) |
| Visual Folds | Pixel-level diff per fold section | Active |

The `features/` directory additionally provides:
- **Sticky element comparison** — presence, count, and layout of fixed/sticky elements at scroll positions
- **Popup detection** — auto-detects modals via multiple trigger strategies (auto-appear, scroll, tab-switch, exit-intent)

---

## Known Limitations

- **Images comparator disabled** — perceptual hash threshold causes false positives; the empty-alt bug was fixed but phash tuning is pending.
- **No general body-text/paragraph comparison** — only headings catch text changes. Fold-based visual diffing is the stopgap.
- **No query-param ignore list** — `utm_*` tracking params on links register as real differences.
- **Fold pixel-diff noise floor not calibrated** — font antialiasing and image re-encoding always produce a nonzero diff percentage.
- **Annotation label overlap** — on mobile viewports, labels from nearby mismatches can visually overlap (deferred to frontend).
