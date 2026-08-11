import os
import json
import uuid
import threading
import queue
import subprocess
import sys

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CORE_DIR = os.path.join(BASE_DIR, "core")

app = FastAPI()

# In-memory job store: job_id -> {status, slug, error}
jobs = {}
job_queue = queue.Queue()


class CompareRequest(BaseModel):
    reference_url: str
    live_url: str
    slug: str | None = None


def run_job(job_id: str, ref_url: str, live_url: str, slug: str):
    jobs[job_id]["status"] = "running"
    try:
        # Capture reference
        subprocess.run(
            [sys.executable, os.path.join(CORE_DIR, "capture.py"),
             "--url", ref_url, "--mode", "reference", "--slug", slug],
            check=True, cwd=CORE_DIR
        )
        # Capture live
        subprocess.run(
            [sys.executable, os.path.join(CORE_DIR, "capture.py"),
             "--url", live_url, "--mode", "live", "--slug", slug],
            check=True, cwd=CORE_DIR
        )
        # Compare
        subprocess.run(
            [sys.executable, os.path.join(CORE_DIR, "compare.py"), "--slug", slug],
            check=True, cwd=CORE_DIR
        )
        jobs[job_id]["status"] = "done"
    except subprocess.CalledProcessError as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)


def worker():
    while True:
        job_id, ref_url, live_url, slug = job_queue.get()
        run_job(job_id, ref_url, live_url, slug)
        job_queue.task_done()


threading.Thread(target=worker, daemon=True).start()


@app.post("/api/compare")
def create_comparison(req: CompareRequest):
    slug = req.slug or f"job-{uuid.uuid4().hex[:8]}"
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "queued", "slug": slug, "error": None}
    job_queue.put((job_id, req.reference_url, req.live_url, slug))
    return {"job_id": job_id, "slug": slug}


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
    return FileResponse(os.path.join(BASE_DIR, "frontend", "compare.html"))


app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)