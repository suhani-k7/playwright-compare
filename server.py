import os
import json
import uuid
import threading
import queue
import subprocess
import sys
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CORE_DIR = os.path.join(BASE_DIR, "core")

app = FastAPI()

# In-memory job store: job_id -> {status, progress, slug, error}
jobs = {}
job_queue = queue.Queue()


class CompareRequest(BaseModel):
    reference_url: str
    live_url: str
    slug: str | None = None
    force_refresh: bool = False


def get_existing_slug_reference_url(slug: str) -> str | None:
    # Try desktop first, then other viewports to extract the canonical tag URL
    for dev in ["desktop", "android", "ios"]:
        path = os.path.join(DATA_DIR, "reference", slug, dev, f"reference-{dev}-{slug}-elements.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                canonical = data.get("canonical", [])
                if canonical and isinstance(canonical, list) and len(canonical) > 0:
                    return canonical[0].get("href")
            except Exception:
                pass
    return None


def run_capture(mode: str, url: str, slug: str, timeout: int = 300):
    result = subprocess.run(
        [sys.executable, os.path.join(CORE_DIR, "capture.py"),
         "--url", url, "--mode", mode, "--slug", slug],
        capture_output=True, text=True, cwd=CORE_DIR, timeout=timeout
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or "No output"
        raise RuntimeError(f"capture.py [{mode}] exited {result.returncode}:\n{detail}")

def run_job(job_id: str, ref_url: str, live_url: str, slug: str):
    jobs[job_id]["status"] = "running"
    try:
        # Phase 1: Capture reference
        jobs[job_id]["progress"] = "Capturing reference site..."
        run_capture("reference", ref_url, slug)
        
        # Phase 2: Capture live
        jobs[job_id]["progress"] = "Capturing live site..."
        run_capture("live", live_url, slug)
        
        # Phase 3: Compare
        jobs[job_id]["progress"] = "Comparing reference vs live..."
        result = subprocess.run(
            [sys.executable, os.path.join(CORE_DIR, "compare.py"), "--slug", slug],
            capture_output=True, text=True, cwd=CORE_DIR, timeout=120
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(f"compare.py exited {result.returncode}:\n{stderr}")
        
        jobs[job_id]["progress"] = "Finished"
        jobs[job_id]["status"] = "done"
    except subprocess.TimeoutExpired:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["progress"] = "Timed out"
        jobs[job_id]["error"] = "Subprocess timed out — the URL may be too slow to load"
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["progress"] = "Failed with exception"
        jobs[job_id]["error"] = str(e)


def worker():
    while True:
        job_id, ref_url, live_url, slug = job_queue.get()
        run_job(job_id, ref_url, live_url, slug)
        job_queue.task_done()


threading.Thread(target=worker, daemon=True).start()


@app.post("/api/compare")
def create_comparison(req: CompareRequest):
    slug = req.slug
    if not slug:
        try:
            path = urlparse(req.reference_url).path
            segments = [s for s in path.split("/") if s]
            slug = segments[-1] if segments else "home"
        except Exception:
            slug = f"job-{uuid.uuid4().hex[:8]}"

    # Normalize URLs for check
    def normalize_url(u: str) -> str:
        u = u.strip().lower()
        if u.endswith("/"):
            u = u[:-1]
        return u

    # Check manifest and slug collision if force_refresh is False
    if not req.force_refresh:
        manifest_path = os.path.join(DATA_DIR, "manifest.json")
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                
                # Check if slug exists in manifest
                exists_in_manifest = any(item.get("slug") == slug for item in manifest)
                if exists_in_manifest:
                    existing_ref = get_existing_slug_reference_url(slug)
                    if existing_ref:
                        if normalize_url(existing_ref) == normalize_url(req.reference_url):
                            # Perfect match, skip capturing
                            return {"status": "already_exists", "slug": slug}
                        else:
                            # Collision: slug is used for another URL
                            return {
                                "status": "collision",
                                "slug": slug,
                                "existing_url": existing_ref,
                                "message": f"Slug '{slug}' is already in use for a different URL: {existing_ref}"
                            }
            except Exception:
                pass

    # Otherwise, kick off the job
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "queued", "progress": "Queued in background...", "slug": slug, "error": None}
    job_queue.put((job_id, req.reference_url, req.live_url, slug))
    return {"status": "started", "job_id": job_id, "slug": slug}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/reports/{slug}")
def get_report(slug: str):
    path = os.path.join(DATA_DIR, "reports", f"{slug}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Report not found")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "frontend", "landing.html"))


@app.get("/results")
def results():
    return FileResponse(os.path.join(BASE_DIR, "frontend", "results.html"))


app.mount("/frontend", StaticFiles(directory=os.path.join(BASE_DIR, "frontend")), name="frontend")
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)