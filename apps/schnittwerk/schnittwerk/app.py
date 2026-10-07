"""HTTP API and static frontend."""

import hmac
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import download, jobs
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
LENGTHS = {"15-30": (15, 30), "30-60": (30, 60), "60-90": (60, 90)}



@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=jobs.cleanup_loop, daemon=True).start()
    yield


app = FastAPI(title="Schnittwerk", docs_url=None, redoc_url=None, lifespan=lifespan)


def require_token(request: Request):
    """Optional shared access token, as Bearer header or ?token= (needed for <video src> and downloads)."""
    if not settings.access_token:
        return
    header = request.headers.get("authorization", "")
    given = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("token", "")
    if not hmac.compare_digest(given.encode(), settings.access_token.encode()):
        raise HTTPException(401, "Zugangscode fehlt oder ist falsch.")


def _options(length: str, count: int, language: str, use_ai: bool) -> dict:
    if length not in LENGTHS:
        raise HTTPException(422, "Unbekannte Cliplänge.")
    min_len, max_len = LENGTHS[length]
    return {"min_len": min_len, "max_len": max_len, "count": max(1, min(12, count)),
            "language": language if language and language != "auto" else None, "use_ai": use_ai}


def _job_or_404(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job nicht gefunden. Er wurde gelöscht oder der Server neu gestartet.")
    return job


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/config")
def config():
    return {"auth": bool(settings.access_token), "claude": settings.claude_enabled,
            "maxUploadMb": settings.max_upload_mb, "maxDurationMin": settings.max_duration_min}


@app.get("/api/auth", dependencies=[Depends(require_token)])
def auth_check():
    return {"ok": True}


class UrlJob(BaseModel):
    url: str = Field(max_length=2000)
    length: str = "30-60"
    count: int = 5
    language: str = "auto"
    use_ai: bool = True


@app.post("/api/jobs/url", dependencies=[Depends(require_token)])
def create_from_url(body: UrlJob):
    try:
        url = download.validate_url(body.url)
    except download.DownloadError as exc:
        raise HTTPException(422, str(exc)) from exc
    job = jobs.new_job(_options(body.length, body.count, body.language, body.use_ai))
    jobs.start_from_url(job, url)
    return {"id": job["id"]}


@app.post("/api/jobs/upload", dependencies=[Depends(require_token)])
async def create_from_upload(file: UploadFile = File(...), length: str = Form("30-60"), count: int = Form(5),
                             language: str = Form("auto"), use_ai: bool = Form(True)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise HTTPException(422, "Bitte eine Videodatei hochladen (MP4, MOV, WebM, MKV).")
    opts = _options(length, count, language, use_ai)
    job = jobs.new_job(opts)
    target = jobs.job_dir(job["id"]) / f"source{suffix}"
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    with target.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                target.unlink(missing_ok=True)
                jobs._update(jobs._jobs, job["id"], status="error", error="Datei zu groß.")
                raise HTTPException(413, f"Die Datei ist größer als {settings.max_upload_mb} MB.")
            out.write(chunk)
    title = Path(file.filename or "Video").stem
    jobs.start_from_file(job, target, title)
    return {"id": job["id"]}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_token)])
def job_status(job_id: str):
    return jobs.public(_job_or_404(job_id))


@app.get("/api/jobs/{job_id}/source", dependencies=[Depends(require_token)])
def job_source(job_id: str):
    job = _job_or_404(job_id)
    if not job.get("source"):
        raise HTTPException(409, "Das Video ist noch nicht bereit.")
    return FileResponse(job["source"])


class RenderRequest(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str = Field("", max_length=200)
    hook: str = Field("", max_length=120)
    aspect: Literal["9:16", "1:1", "16:9"] = "9:16"
    fill: Literal["crop", "blur"] = "crop"
    crop: float = Field(0.5, ge=0, le=1)
    captions: bool = True
    cap_style: Literal["karaoke", "bold", "minimal"] = "karaoke"


@app.post("/api/jobs/{job_id}/renders", dependencies=[Depends(require_token)])
def create_render(job_id: str, body: RenderRequest):
    job = _job_or_404(job_id)
    if job["status"] != "ready":
        raise HTTPException(409, "Die Analyse läuft noch.")
    start, end = min(body.start, job["duration"]), min(body.end, job["duration"])
    if end - start < 1:
        raise HTTPException(422, "Der Clip muss mindestens 1 Sekunde lang sein.")
    if end - start > 600:
        raise HTTPException(422, "Clips sind auf 10 Minuten begrenzt.")
    req = body.model_dump() | {"start": start, "end": end}
    render = jobs.new_render(job, req)
    return {"id": render["id"]}


@app.get("/api/renders/{render_id}", dependencies=[Depends(require_token)])
def render_status(render_id: str):
    r = jobs.get_render(render_id)
    if not r:
        raise HTTPException(404, "Export nicht gefunden.")
    return {k: r[k] for k in ("id", "status", "progress", "error", "filename")}


@app.get("/api/renders/{render_id}/file", dependencies=[Depends(require_token)])
def render_file(render_id: str, download_: bool = Query(False, alias="download")):
    r = jobs.get_render(render_id)
    if not r or r["status"] != "done":
        raise HTTPException(404, "Export nicht verfügbar.")
    return FileResponse(r["path"], media_type="video/mp4", filename=r["filename"],
                        content_disposition_type="attachment" if download_ else "inline")
