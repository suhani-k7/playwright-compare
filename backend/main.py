import os
import json
import uuid
from pathlib import Path
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import runner, schemas
from .cache import comparison_cache
from .exporter import bake_annotated_image

app = FastAPI(title="Website Comparison Service")

# CORS — allow Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMPARISONS_ROOT = PROJECT_ROOT / "comparisons"
COMPARISONS_ROOT.mkdir(exist_ok=True)

# Mount comparisons static files directory so screenshots/diffs are publicly accessible
app.mount("/comparisons", StaticFiles(directory=str(COMPARISONS_ROOT)), name="comparisons")

@app.post("/api/compare", response_model=schemas.CompareResponse)
async def compare(
    request: schemas.CompareRequest,
    background_tasks: BackgroundTasks
):
    """
    Kicks off a comparison run. If a run for the same URL pair is cached and valid,
    returns the cached run_id. Otherwise, starts a new run.
    """
    ref_url = request.reference_url.strip()
    live_url = request.live_url.strip()

    if not ref_url or not live_url:
        raise HTTPException(status_code=400, detail="Both reference_url and live_url are required.")

    # Check comparison cache
    cached_run_id = comparison_cache.get(ref_url, live_url)
    if cached_run_id:
        info = runner.get_run_info(cached_run_id)
        # If the cached run exists and did not fail, reuse it
        if info and info["status"] != "failed":
            print(f"[api] Reusing cached comparison run_id={cached_run_id} for URL pair.")
            return schemas.CompareResponse(run_id=cached_run_id)

    # Start new comparison run
    run_id = uuid.uuid4().hex
    print(f"[api] Initiating new comparison run_id={run_id}")
    runner.register_run(run_id, ref_url, live_url)
    comparison_cache.set(ref_url, live_url, run_id)
    
    background_tasks.add_task(runner.run_comparison, run_id)
    return schemas.CompareResponse(run_id=run_id)

@app.get("/api/compare/{run_id}", response_model=schemas.StatusResponse)
async def get_status(run_id: str):
    """
    Returns the running status of a comparison job.
    """
    info = runner.get_run_info(run_id)
    if not info:
        raise HTTPException(status_code=404, detail="Comparison run not found.")
    return schemas.StatusResponse(
        run_id=run_id,
        status=info["status"],
        error=info.get("error")
    )

@app.get("/api/compare/{run_id}/results")
async def get_results(run_id: str):
    """
    Aggregates all results for a done comparison job.
    """
    info = runner.get_run_info(run_id)
    if not info:
        raise HTTPException(status_code=404, detail="Comparison run not found.")
    
    if info["status"] == "failed":
        raise HTTPException(status_code=500, detail=f"Comparison run failed: {info.get('error')}")
    
    if info["status"] != "done":
        raise HTTPException(status_code=400, detail=f"Comparison run is not done yet. Status: {info['status']}")

    run_dir = COMPARISONS_ROOT / run_id
    meta_file = run_dir / "meta.json"
    seo_file = run_dir / "seo" / "diff.json"

    if not meta_file.exists():
        raise HTTPException(status_code=500, detail="Metadata file missing from run folder.")

    with open(meta_file, "r", encoding="utf-8") as f:
        meta_data = json.load(f)

    seo_data = {}
    if seo_file.exists():
        with open(seo_file, "r", encoding="utf-8") as f:
            seo_data = json.load(f)

    # Gather viewport + section results
    results = {}
    viewports = ["desktop", "ios", "android"]
    sections = ["fullpage", "firstfold", "sticky", "popup"]

    for vp in viewports:
        results[vp] = {}
        for sec in sections:
            diff_file = run_dir / vp / sec / "diff.json"
            issues_data = []
            if diff_file.exists():
                try:
                    with open(diff_file, "r", encoding="utf-8") as f:
                        issues_data = json.load(f).get("issues", [])
                except Exception as e:
                    print(f"[api] Error reading diff for {vp}/{sec}: {e}")
            
            # Form screenshot relative URLs
            ref_screenshot = f"/comparisons/{run_id}/{vp}/{sec}/ref.png"
            live_screenshot = f"/comparisons/{run_id}/{vp}/{sec}/live.png"
            
            # Check if annotated exists (in case user previously exported it)
            annotated_file = run_dir / vp / sec / "live-annotated.png"
            annotated_screenshot = f"/comparisons/{run_id}/{vp}/{sec}/live-annotated.png" if annotated_file.exists() else None

            results[vp][sec] = {
                "issues": issues_data,
                "screenshots": {
                    "reference": ref_screenshot,
                    "live": live_screenshot,
                    "annotated": annotated_screenshot
                }
            }

    return JSONResponse(content={
        "run_id": run_id,
        "reference_url": meta_data.get("reference_url"),
        "live_url": meta_data.get("live_url"),
        "timestamp": meta_data.get("timestamp"),
        "seo": seo_data,
        "results": results
    })

@app.post("/api/compare/{run_id}/export", response_model=schemas.ExportResponse)
async def export_annotated(run_id: str, request: schemas.ExportRequest):
    """
    On-demand annotation generator. Draws red mismatch boxes server-side
    and saves to live-annotated.png.
    """
    info = runner.get_run_info(run_id)
    if not info:
        raise HTTPException(status_code=404, detail="Comparison run not found.")
    if info["status"] != "done":
        raise HTTPException(status_code=400, detail="Cannot export results of an incomplete run.")

    try:
        url = bake_annotated_image(run_id, request.viewport, request.section)
        return schemas.ExportResponse(annotated_url=url)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")

@app.get("/api/health")
async def health():
    return {"status": "ok"}