"""
Live planner API for the web dashboard.

The browser sends a place name and a budget; this server runs the same
OSM -> ML -> Fuzzy -> GA pipeline as `python run_pipeline.py place --fast`
and returns the dashboard data. Jobs run one at a time in a background
thread; the page polls for progress.

    uvicorn server.app:app --port 7860

Endpoints
    GET  /api/health                 server is up, queue length
    POST /api/plan                   {"boundary" + "name" | "place", "budget", "stations"?, "spacing"?} -> {"job"}
    GET  /api/jobs/{job}             status, progress message and fraction
    GET  /api/jobs/{job}/result      dashboard data (JSON)
    GET  /api/jobs/{job}/result.js   the same as `window.DASHBOARD_DATA = ...;` for a <script> tag
"""

import hashlib
import json
import os
import queue
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, model_validator

from src.pipeline.place_pipeline import PlaceSettings, run_place, slugify

WORK = Path(os.environ.get("EV_WORK_DIR", "/tmp/ev-planner"))
MAX_QUEUE = int(os.environ.get("EV_MAX_QUEUE", "6"))
MAX_AREA_KM2 = float(os.environ.get("EV_MAX_AREA_KM2", "12"))
KEEP_RESULTS = 40

app = FastAPI(title="EV charging site planner", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


MAX_BOUNDARY_BYTES = 200_000


class PlanRequest(BaseModel):
    place: Optional[str] = Field(default=None, min_length=3, max_length=120, description="Name to search for")
    boundary: Optional[Dict[str, Any]] = Field(default=None, description="GeoJSON Polygon/MultiPolygon drawn on the map")
    name: Optional[str] = Field(default=None, min_length=1, max_length=80, description="Display name for a drawn area")
    budget: float = Field(ge=10, le=5000, description="Budget in INR lakhs")
    stations: Optional[int] = Field(default=None, ge=1, le=8, description="Fixed count; empty = best the budget allows")
    spacing: float = Field(default=300, ge=100, le=1000, description="Minimum metres between stations")

    @model_validator(mode="after")
    def area_given(self):
        if not self.place and not self.boundary:
            raise ValueError("Give a place name or draw the area on the map.")
        if self.boundary is not None:
            if self.boundary.get("type") not in ("Polygon", "MultiPolygon") or not self.boundary.get("coordinates"):
                raise ValueError("The boundary must be a GeoJSON Polygon or MultiPolygon.")
            if len(json.dumps(self.boundary)) > MAX_BOUNDARY_BYTES:
                raise ValueError("The boundary has too many points. Draw it with fewer points.")
        return self

    @property
    def label(self) -> str:
        return " ".join((self.name or self.place or "Drawn area").split())


class Job:
    def __init__(self, req: PlanRequest, client: str):
        self.id = uuid.uuid4().hex[:12]
        self.req = req
        self.client = client
        self.status = "queued"
        self.message = "Waiting for the planner"
        self.progress = 0.0
        self.error: Optional[str] = None
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None

    @property
    def key(self) -> str:
        r = self.req
        area = hashlib.sha1(json.dumps(r.boundary, sort_keys=True).encode()).hexdigest()[:12] if r.boundary else ""
        return f"{slugify(r.label)}|{area}|{r.budget:g}|{r.stations or 'auto'}|{r.spacing:g}"

    def public(self) -> dict:
        now = self.finished or time.time()
        return {"job": self.id, "status": self.status, "message": self.message, "progress": round(self.progress, 3),
                "error": self.error, "place": self.req.label, "budget": self.req.budget, "stations": self.req.stations,
                "position": position(self), "elapsed_s": round(now - (self.started or now), 1)}


jobs: "OrderedDict[str, Job]" = OrderedDict()
by_key: dict = {}
pending: "queue.Queue[Job]" = queue.Queue()
lock = threading.Lock()


def position(job: Job) -> int:
    """How many jobs are ahead of this one (0 when running or finished)."""
    if job.status != "queued":
        return 0
    with lock:
        return sum(1 for j in jobs.values() if j.status in ("queued", "running") and j.created < job.created)


def result_path(job_id: str) -> Path:
    return WORK / "results" / f"{job_id}.json"


def worker() -> None:
    while True:
        job = pending.get()
        job.status, job.started = "running", time.time()

        def report(msg: str, frac: float) -> None:
            job.message, job.progress = msg, frac

        try:
            settings = PlaceSettings.fast(budget_lakhs=job.req.budget, n_stations=job.req.stations,
                                          spacing_m=job.req.spacing, max_area_km2=MAX_AREA_KM2)
            res = run_place(place=job.req.place, boundary=job.req.boundary, name=job.req.name if job.req.boundary else None,
                            settings=settings, progress=report, export_web=False,
                            data_root=str(WORK / "data"), out_root=str(WORK / "outputs"))
            path = result_path(job.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(res["web_data"], separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
            job.status, job.message, job.progress = "done", "Done", 1.0
        except RuntimeError as exc:  # the pipeline's own, user-readable reasons (area too big, budget too low...)
            job.status, job.error = "error", str(exc)
        except Exception as exc:  # network failures, unexpected data
            traceback.print_exc()
            job.status, job.error = "error", f"The planner could not finish this place ({type(exc).__name__}). Try again or a more specific name."
        finally:
            job.finished = time.time()
            prune()


def prune() -> None:
    """Keep the newest KEEP_RESULTS finished jobs."""
    with lock:
        done = [j for j in jobs.values() if j.status in ("done", "error")]
        for old in done[:-KEEP_RESULTS]:
            jobs.pop(old.id, None)
            if by_key.get(old.key) == old.id:
                by_key.pop(old.key, None)
            result_path(old.id).unlink(missing_ok=True)


threading.Thread(target=worker, daemon=True).start()


def get_job(job_id: str) -> Job:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "This plan has expired on the server. Plan the place again.")
    return job


@app.get("/")
@app.get("/api/health")
def health() -> dict:
    active = sum(1 for j in jobs.values() if j.status in ("queued", "running"))
    return {"ok": True, "service": "ev-charging-site-planner", "active_jobs": active, "max_area_km2": MAX_AREA_KM2,
            "chargers": bool(os.environ.get("OCM_API_KEY"))}


@app.post("/api/plan")
def plan(req: PlanRequest, request: Request) -> JSONResponse:
    if req.place:
        req.place = " ".join(req.place.split())
    probe = Job(req, request.client.host if request.client else "?")
    with lock:
        same = by_key.get(probe.key)
        if same and same in jobs and jobs[same].status != "error":
            return JSONResponse(jobs[same].public())  # identical request: reuse the running or finished job
        active = [j for j in jobs.values() if j.status in ("queued", "running")]
        if any(j.client == probe.client for j in active):
            raise HTTPException(429, "You already have a plan running. Wait for it to finish.")
        if len(active) >= MAX_QUEUE:
            raise HTTPException(503, "The planner is busy. Try again in a few minutes.")
        jobs[probe.id] = probe
        by_key[probe.key] = probe.id
    pending.put(probe)
    return JSONResponse(probe.public(), status_code=202)


@app.get("/api/jobs/{job_id}")
def status(job_id: str) -> dict:
    return get_job(job_id).public()


def _result_text(job_id: str) -> str:
    job = get_job(job_id)
    if job.status != "done":
        raise HTTPException(409, f"The plan is {job.status}.")
    return result_path(job_id).read_text(encoding="utf-8")


@app.get("/api/jobs/{job_id}/result")
def result(job_id: str) -> Response:
    return Response(_result_text(job_id), media_type="application/json")


@app.get("/api/jobs/{job_id}/result.js")
def result_js(job_id: str) -> Response:
    try:
        body = f"window.DASHBOARD_DATA = {_result_text(job_id)};\n"
    except HTTPException as exc:
        body = f"window.EV_LIVE_ERROR = {json.dumps(exc.detail)};\n"
    return Response(body, media_type="application/javascript", headers={"Cache-Control": "no-store"})
