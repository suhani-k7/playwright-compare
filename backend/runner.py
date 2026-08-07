import threading
from typing import Dict, Any, Optional
from pathlib import Path

from .crawler_engine import run_crawler_parallel
from .diff_engine import run_diff_engine

# In-memory run status registry
_runs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()

def register_run(run_id: str, reference_url: str, live_url: str) -> None:
    with _lock:
        _runs[run_id] = {
            "status": "pending",
            "reference_url": reference_url,
            "live_url": live_url,
            "error": None
        }

def get_run_info(run_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        return _runs.get(run_id)

def _set_status(run_id: str, status: str, error: str = None) -> None:
    with _lock:
        if run_id in _runs:
            _runs[run_id]["status"] = status
            if error:
                _runs[run_id]["error"] = error

def run_comparison(run_id: str) -> None:
    info = get_run_info(run_id)
    if not info:
        return

    _set_status(run_id, "running")
    ref_url = info["reference_url"]
    live_url = info["live_url"]

    try:
        # Step 1: Run parallel Playwright crawlers
        crawl_results = run_crawler_parallel(run_id, ref_url, live_url)

        # Step 2: Run element alignment and diff engine
        run_diff_engine(run_id, crawl_results, ref_url, live_url)

        _set_status(run_id, "done")
        print(f"[runner] Run {run_id} completed successfully.")
    except Exception as e:
        print(f"[runner] Run {run_id} failed: {e}")
        _set_status(run_id, "failed", error=str(e))