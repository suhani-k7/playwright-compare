from __future__ import annotations
from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel

VALID_CATEGORIES = Literal[
    "headings", "images", "buttons", "links",
    "sticky", "popup", "metadata"
]

class CompareRequest(BaseModel):
    reference_url: str
    live_url: str
    categories: List[str]
    all_annotations: bool = False

class CompareResponse(BaseModel):
    run_id: str

class StatusResponse(BaseModel):
    run_id: str
    status: Literal["pending", "running", "done", "failed"]
    error: Optional[str] = None

class ExportRequest(BaseModel):
    viewport: str
    section: str

class ExportResponse(BaseModel):
    annotated_url: str