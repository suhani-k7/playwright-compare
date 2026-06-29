import json
import os
import uuid

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import runner, schemas

app = FastAPI(title="Comparison Service")

# ---------------------------------------------------------------------------
# CORS — allow Vite dev server
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Static file serving — the entire outputs/ tree is exposed at /screenshots
# ---------------------------------------------------------------------------
OUTPUT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "outputs")
)
os.makedirs(OUTPUT_ROOT, exist_ok=True)
app.mount("/screenshots", StaticFiles(directory=OUTPUT_ROOT), name="screenshots")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/compare", response_model=schemas.CompareResponse)
async def compare(
    request: schemas.CompareRequest,
    background_tasks: BackgroundTasks,
):
    """Start a comparison run.  Returns run_id immediately; poll /status to track progress."""
    run_id = uuid.uuid4().hex
    runner.register_run(run_id, request)
    background_tasks.add_task(runner.run_comparison, run_id)
    return schemas.CompareResponse(run_id=run_id)


@app.get("/status/{run_id}", response_model=schemas.StatusResponse)
async def status(run_id: str):
    info = runner.get_run_info(run_id)
    if not info:
        raise HTTPException(status_code=404, detail="Run not found")
    return schemas.StatusResponse(
        run_id=run_id,
        status=info["status"],
        error=info.get("error"),
    )


@app.get("/results/{run_id}")
async def results(run_id: str):
    info = runner.get_run_info(run_id)
    if not info:
        raise HTTPException(status_code=404, detail="Run not found")
    if info["status"] == "failed":
        raise HTTPException(
            status_code=500,
            detail=f"Run failed: {info.get('error', 'unknown error')}",
        )
    if info["status"] != "done":
        raise HTTPException(status_code=400, detail=f"Run status: {info['status']}")

    merged_path = os.path.join(info["run_dir"], "merged_result.json")
    if not os.path.isfile(merged_path):
        raise HTTPException(status_code=500, detail="merged_result.json missing")

    with open(merged_path, "r", encoding="utf-8") as f:
        return JSONResponse(content=json.load(f))


# ---------------------------------------------------------------------------
# Health check (useful for debugging)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}