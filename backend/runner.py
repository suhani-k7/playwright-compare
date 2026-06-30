from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
import subprocess

from . import schemas

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BACKEND_DIR  = Path(__file__).parent
PROJECT_ROOT = BACKEND_DIR.parent
OUTPUT_ROOT  = PROJECT_ROOT / "outputs"
OUTPUT_ROOT.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# In-memory run registry
# ---------------------------------------------------------------------------
_runs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def register_run(run_id: str, request: schemas.CompareRequest) -> None:
    run_dir = OUTPUT_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    with _lock:
        _runs[run_id] = {
            "status":        "pending",
            "run_dir":       str(run_dir),
            "reference_url": request.reference_url,
            "live_url":      request.live_url,
            "categories":    request.categories,
            "all_annotations": request.all_annotations,
            "error":         None,
        }


def get_run_info(run_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        return _runs.get(run_id)


def _set_status(run_id: str, status: str, error: str = None) -> None:
    with _lock:
        _runs[run_id]["status"] = status
        if error:
            _runs[run_id]["error"] = error


# ---------------------------------------------------------------------------
# Script + output path configuration
# ---------------------------------------------------------------------------

CATEGORY_CONFIG: Dict[str, Dict[str, str]] = {
    "headings":  {"capture": "capture.py",               "compare": "compare.py",               "script_key": "core"},
    "images":    {"capture": "capture.py",               "compare": "compare.py",               "script_key": "core"},
    "buttons":   {"capture": "capture.py",               "compare": "compare.py",               "script_key": "core"},
    "links":     {"capture": "capture.py",               "compare": "compare.py",               "script_key": "core"},
    "metadata":  {"capture": "capture.py",               "compare": "compare.py",               "script_key": "core"},
    "sticky":    {"capture": "sticky/capture-sticky.py", "compare": "sticky/compare-sticky.py", "script_key": "sticky"},
    "popup":     {"capture": "popup/capture-popup.py",   "compare": "popup/compare-popup.py",   "script_key": "popup"},
}

# All paths confirmed from actual script runs. {slug} substituted at runtime.
# Desktop viewport only surfaced to frontend.
CATEGORY_OUTPUTS: Dict[str, Dict[str, str]] = {
    "headings": {
        "report":    "reports/{slug}.json",
        "reference": "reference/desktop-{slug}/reference-desktop-{slug}-screenshot.png",
        "live":      "live/desktop-{slug}/live-desktop-{slug}-screenshot.png",
        "annotated": "diffs/desktop-{slug}-annotated.png",
    },
    "images": {
        "report":    "reports/{slug}.json",
        "reference": "reference/desktop-{slug}/reference-desktop-{slug}-screenshot.png",
        "live":      "live/desktop-{slug}/live-desktop-{slug}-screenshot.png",
        "annotated": "diffs/desktop-{slug}-annotated.png",
    },
    "buttons": {
        "report":    "reports/{slug}.json",
        "reference": "reference/desktop-{slug}/reference-desktop-{slug}-screenshot.png",
        "live":      "live/desktop-{slug}/live-desktop-{slug}-screenshot.png",
        "annotated": "diffs/desktop-{slug}-annotated.png",
    },
    "links": {
        "report":    "reports/{slug}.json",
        "reference": "reference/desktop-{slug}/reference-desktop-{slug}-screenshot.png",
        "live":      "live/desktop-{slug}/live-desktop-{slug}-screenshot.png",
        "annotated": "diffs/desktop-{slug}-annotated.png",
    },
    "metadata": {
        "report":    "reports/{slug}.json",
        "reference": "reference/desktop-{slug}/reference-desktop-{slug}-screenshot.png",
        "live":      "live/desktop-{slug}/live-desktop-{slug}-screenshot.png",
        "annotated": "diffs/desktop-{slug}-annotated.png",
    },
    "sticky": {
        "report":    "sticky/reports/sticky-{slug}.json",
        "reference": "reference/desktop-{slug}/reference-desktop-{slug}-screenshot.png",
        "live":      "live/desktop-{slug}/live-desktop-{slug}-screenshot.png",
        "annotated": "sticky/diffs/annotated-desktop-{slug}-screenshot.png",
    },
    "popup": {
        "report":    "popup/reports/popup-{slug}.json",
        "reference": "reference/desktop-{slug}/reference-desktop-{slug}-screenshot.png",
        "live":      "live/desktop-{slug}/live-desktop-{slug}-screenshot.png",
        "annotated": "popup/diffs/desktop-{slug}-annotated.png",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: List[str], cwd: Path) -> bool:
    """Run cmd with absolute script path, cwd=run_dir for output isolation."""
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[runner] FAILED: {' '.join(cmd)}\n{result.stderr}")
        return False
    return True


def _safe_load_json(path: Path) -> Any:
    if path.is_file():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _resolve_screenshot(run_id: str, rel_path: str) -> Optional[str]:
    if not rel_path:
        return None
    full = OUTPUT_ROOT / run_id / rel_path
    if full.is_file():
        return f"/screenshots/{run_id}/{rel_path}"
    return None


def _normalize_annotations(raw: Any, category: str) -> list:
    if isinstance(raw, dict):
        items = (
            raw.get(category)
            or raw.get("annotations")
            or raw.get("differences")
            or raw.get("issues")
            or []
        )
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    known = {"tag", "text", "content", "alt", "href", "issue_type", "status", "bbox"}
    normalised = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalised.append({
            "tag":        item.get("tag"),
            "text":       item.get("text") or item.get("content"),
            "alt":        item.get("alt"),
            "href":       item.get("href"),
            "issue_type": item.get("issue_type") or item.get("status"),
            "bbox":       item.get("bbox"),
            "extra":      {k: v for k, v in item.items() if k not in known},
        })
    return normalised


def _substitute_slug(template: str, slug: str) -> str:
    return template.replace("{slug}", slug)


def _copy_project_outputs_to_run_dir(run_dir: Path, slug: str, script_key: str) -> None:
    """Copy generated outputs from project root subfolders to the run_dir."""
    if script_key == "core":
        src_dirs = {
            PROJECT_ROOT / "reference": run_dir / "reference",
            PROJECT_ROOT / "live": run_dir / "live",
            PROJECT_ROOT / "diffs": run_dir / "diffs",
            PROJECT_ROOT / "reports": run_dir / "reports",
        }
    elif script_key == "sticky":
        src_dirs = {
            PROJECT_ROOT / "sticky" / "reference": run_dir / "reference",
            PROJECT_ROOT / "sticky" / "live": run_dir / "live",
            PROJECT_ROOT / "sticky" / "diffs": run_dir / "sticky" / "diffs",
            PROJECT_ROOT / "sticky" / "reports": run_dir / "sticky" / "reports",
        }
    elif script_key == "popup":
        src_dirs = {
            PROJECT_ROOT / "popup" / "reference": run_dir / "reference",
            PROJECT_ROOT / "popup" / "live": run_dir / "live",
            PROJECT_ROOT / "popup" / "diffs": run_dir / "popup" / "diffs",
            PROJECT_ROOT / "popup" / "reports": run_dir / "popup" / "reports",
        }
    else:
        src_dirs = {}

    # Copy files and delete them from source
    for src_dir, dest_dir in src_dirs.items():
        if not src_dir.is_dir():
            continue
        for item in list(src_dir.rglob(f"*{slug}*")):
            if item.is_file():
                rel_path = item.relative_to(src_dir)
                dest_file = dest_dir / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest_file)
                print(f"[runner] Copied project output: {item} -> {dest_file}")
                try:
                    item.unlink()
                except Exception as e:
                    print(f"[runner] Failed to delete temporary source file {item}: {e}")

    # Clean up empty source subdirectories containing slug
    for src_dir in src_dirs.keys():
        if not src_dir.is_dir():
            continue
        # Search for directories containing the slug
        for item in list(src_dir.rglob(f"*{slug}*")):
            if item.is_dir():
                try:
                    if not any(item.iterdir()):
                        item.rmdir()
                        print(f"[runner] Removed empty temp directory: {item}")
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Main entry point — called by FastAPI BackgroundTasks
# ---------------------------------------------------------------------------

def _prune_outputs(keep: int = 5) -> None:
    """Delete oldest output run directories, preserving the most recent `keep` runs.
    The current run (if present) is always retained because its folder is among the newest.
    """
    # Gather all run directories sorted by modification time (newest first)
    run_dirs = [d for d in OUTPUT_ROOT.iterdir() if d.is_dir()]
    run_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    # Remove any beyond the `keep` most recent
    for old_dir in run_dirs[keep:]:
        try:
            shutil.rmtree(old_dir)
            print(f"[runner] Pruned old output run: {old_dir}")
        except Exception as e:
            print(f"[runner] Failed to prune {old_dir}: {e}")

# Call prune at the start of run_comparison to keep only the latest 5 runs
def run_comparison(run_id: str) -> None:
    # Ensure we keep only the latest 5 output runs before proceeding
    _prune_outputs(keep=5)
    info = get_run_info(run_id)
    if not info:
        return

    _set_status(run_id, "running")

    run_dir    = Path(info["run_dir"])
    info = get_run_info(run_id)
    if not info:
        return

    _set_status(run_id, "running")

    run_dir    = Path(info["run_dir"])
    ref_url    = info["reference_url"]
    live_url   = info["live_url"]
    categories: List[str] = info["categories"]

    scripts_run: set              = set()
    all_screenshots: Dict[str, dict] = {}
    all_annotations: Dict[str, list] = {}

    try:
        for cat in categories:
            cfg = CATEGORY_CONFIG.get(cat)
            if not cfg:
                print(f"[runner] Unknown category '{cat}', skipping.")
                continue

            script_key = cfg["script_key"]
            slug = f"{run_id[:8]}-{script_key}"

            if script_key not in scripts_run:
                # Resolve absolute paths to scripts so Python finds them
                # regardless of cwd. cwd=run_dir keeps output writes isolated.
                capture_abs = str(PROJECT_ROOT / cfg["capture"])
                compare_abs = str(PROJECT_ROOT / cfg["compare"])

                # Step 1: capture reference
                _run(
                    [sys.executable, capture_abs,
                     "--url", ref_url, "--mode", "reference", "--slug", slug],
                    cwd=PROJECT_ROOT,
                )
                # Step 2: capture live
                _run(
                    [sys.executable, capture_abs,
                     "--url", live_url, "--mode", "live", "--slug", slug],
                    cwd=PROJECT_ROOT,
                )
                # Step 3: compare
                compare_cmd = [sys.executable, compare_abs, "--slug", slug]
                if info.get("all_annotations"):
                    compare_cmd.append("--all")
                _run(
                    compare_cmd,
                    cwd=PROJECT_ROOT,
                )

                _copy_project_outputs_to_run_dir(run_dir, slug, script_key)

                scripts_run.add(script_key)

            # Resolve output paths with slug substituted
            paths         = CATEGORY_OUTPUTS.get(cat, {})
            report_rel    = _substitute_slug(paths.get("report",    ""), slug)
            ref_rel       = _substitute_slug(paths.get("reference", ""), slug)
            live_rel      = _substitute_slug(paths.get("live",      ""), slug)
            annotated_rel = _substitute_slug(paths.get("annotated", ""), slug)

            raw_report = _safe_load_json(run_dir / report_rel)

            all_annotations[cat] = _normalize_annotations(raw_report, cat)
            all_screenshots[cat] = {
                "reference": _resolve_screenshot(run_id, ref_rel),
                "live":      _resolve_screenshot(run_id, live_rel),
                "annotated": _resolve_screenshot(run_id, annotated_rel),
            }

        merged = {
            "run_id":        run_id,
            "reference_url": ref_url,
            "live_url":      live_url,
            "categories":    categories,
            "screenshots":   all_screenshots,
            "annotations":   all_annotations,
        }
        with open(run_dir / "merged_result.json", "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)

        _set_status(run_id, "done")

    except Exception as exc:
        _set_status(run_id, "failed", error=str(exc))
        raise