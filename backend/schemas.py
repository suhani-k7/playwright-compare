from __future__ import annotations
from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, HttpUrl

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

VALID_CATEGORIES = Literal[
    "headings", "images", "buttons", "links",
    "sticky", "popup", "metadata"
]


class CompareRequest(BaseModel):
    reference_url: str
    live_url: str
    categories: List[str]  # subset of VALID_CATEGORIES


class CompareResponse(BaseModel):
    run_id: str


class StatusResponse(BaseModel):
    run_id: str
    status: Literal["pending", "running", "done", "failed"]
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Annotation / Results models
# ---------------------------------------------------------------------------

class BBox(BaseModel):
    x: float
    y: float
    width: float
    height: float


class Annotation(BaseModel):
    tag: Optional[str] = None
    text: Optional[str] = None
    alt: Optional[str] = None
    href: Optional[str] = None
    issue_type: Optional[str] = None   # Missing | Extra | Modified
    bbox: Optional[BBox] = None
    extra: Optional[Dict[str, Any]] = None   # any leftover keys


class Screenshots(BaseModel):
    reference: Optional[str] = None
    live: Optional[str] = None
    annotated: Optional[str] = None


class ResultsResponse(BaseModel):
    run_id: str
    reference_url: str
    live_url: str
    categories: List[str]
    screenshots: Dict[str, Screenshots]        # keyed by category slug
    annotations: Dict[str, List[Annotation]]   # keyed by category slug