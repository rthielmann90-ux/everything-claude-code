"""In-memory job store and the processing pipeline.

Jobs live in memory and in SCHNITTWERK_DATA_DIR/jobs/<job id>/. A restart forgets
running jobs; finished files are cleaned up after SCHNITTWERK_JOB_TTL_HOURS.
"""

import logging
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import captions, download, highlights, media
from .config import settings

log = logging.getLogger("schnittwerk")

_jobs: dict[str, dict] = {}
_renders: dict[str, dict] = {}
_lock = threading.Lock()
_analysis_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analyse")
_render_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="render")

PUBLIC_JOB_FIELDS = ("id", "status", "progress", "message", "error", "title", "duration", "width", "height",
                     "has_audio", "language", "segments", "chunks", "energy", "clips", "method")


def _update(store: dict, item_id: str, **fields):
    with _lock:
        if item_id in store:
            store[item_id].update(fields)


def jobs_root() -> Path:
    return settings.data_dir / "jobs"


def job_dir(job_id: str) -> Path:
    return jobs_root() / job_id


def new_job(options: dict) -> dict:
    job_id = secrets.token_urlsafe(12)
    job_dir(job_id).mkdir(parents=True, exist_ok=True)
    job = {"id": job_id, "status": "queued", "progress": 0, "message": "Wartet auf freien Platz…",
           "error": None, "title": "", "created": time.time(), "options": options, "source": None,
           "segments": [], "chunks": [], "energy": [], "clips": [], "method": ""}
    with _lock:
        _jobs[job_id] = job
    return job


def get_job(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def public(job: dict) -> dict:
    return {k: job.get(k) for k in PUBLIC_JOB_FIELDS}


def start_from_url(job: dict, url: str):
    _analysis_pool.submit(_run, job["id"], url, None)


def start_from_file(job: dict, path: Path, title: str):
    _analysis_pool.submit(_run, job["id"], None, (path, title))


def _run(job_id: str, url: str | None, upload: tuple[Path, str] | None):
    try:
        _pipeline(job_id, url, upload)
    except (download.DownloadError, media.MediaError) as exc:
        _update(_jobs, job_id, status="error", error=str(exc), message=str(exc))
    except Exception as exc:  # noqa: BLE001 - surface any failure to the user instead of a stuck job
        log.exception("job %s failed", job_id)
        _update(_jobs, job_id, status="error", error="Interner Fehler: " + str(exc)[:200],
                message="Verarbeitung fehlgeschlagen.")


def _pipeline(job_id: str, url: str | None, upload: tuple[Path, str] | None):
    job = get_job(job_id)
    opts = job["options"]
    folder = job_dir(job_id)

    if url:
        _update(_jobs, job_id, status="downloading", progress=2, message="Video wird heruntergeladen…")
        src, title = download.download(
            url, folder, settings.max_upload_mb * 1024 * 1024, settings.max_duration_min * 60,
            lambda p: _update(_jobs, job_id, progress=2 + round(p * 28)))
    else:
        src, title = upload

    _update(_jobs, job_id, status="analyzing", progress=30, message="Video wird geprüft…", title=title)
    info = media.probe(src)
    if info["duration"] > settings.max_duration_min * 60:
        raise media.MediaError(f"Das Video ist länger als {settings.max_duration_min} Minuten.")
    _update(_jobs, job_id, source=str(src), **info)

    segments, words, energy, language = [], [], [], None
    if info["has_audio"]:
        _update(_jobs, job_id, progress=32, message="Tonspur wird gelesen…")
        wav = folder / "audio.wav"
        media.extract_audio(src, wav)
        energy = media.loudness(wav)
        _update(_jobs, job_id, energy=energy, status="transcribing", progress=35,
                message="Sprache wird erkannt (Whisper)…")
        from . import transcribe
        segments, words, language = transcribe.transcribe(
            wav, info["duration"], opts.get("language"),
            lambda p: _update(_jobs, job_id, progress=35 + round(p * 45)))
        wav.unlink(missing_ok=True)

    chunks = captions.build_chunks(words)
    _update(_jobs, job_id, segments=segments, chunks=chunks, language=language, status="analyzing",
            progress=82, message="Die stärksten Momente werden gesucht…")

    min_len, max_len, count = opts["min_len"], opts["max_len"], opts["count"]
    clips, method = [], ""
    if opts.get("use_ai") and segments and settings.claude_enabled:
        _update(_jobs, job_id, message="Claude liest das Transkript…")
        try:
            clips = highlights.with_claude(segments, info["duration"], min_len, max_len, count,
                                           settings.claude_model)
            method = "Ausgewählt von Claude anhand des Transkripts."
        except highlights.ClaudeUnavailableError as exc:
            method = f"{exc} Auswahl stattdessen nach Tonspur."
    if not clips:
        clips = highlights.heuristic(energy, segments, info["duration"], min_len, max_len, count)
        method = method or ("Ausgewählt nach Lautstärke und Dynamik der Tonspur." if energy
                            else "Keine Tonspur, Clips gleichmäßig verteilt.")
    if not segments:
        method += " Keine Sprache erkannt, daher keine Untertitel."
    for i, c in enumerate(clips):
        c["id"] = i + 1
    _update(_jobs, job_id, clips=clips, method=method, status="ready", progress=100, message="Fertig")


# ---------- Rendering ----------

def new_render(job: dict, req: dict) -> dict:
    render_id = secrets.token_urlsafe(12)
    render = {"id": render_id, "job_id": job["id"], "status": "queued", "progress": 0, "error": None,
              "path": None, "filename": None, "created": time.time()}
    with _lock:
        _renders[render_id] = render
    _render_pool.submit(_render, render_id, job, req)
    return render


def get_render(render_id: str) -> dict | None:
    with _lock:
        r = _renders.get(render_id)
        return dict(r) if r else None


def _slug(text: str) -> str:
    import re
    import unicodedata
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:60] or "clip"


def _render(render_id: str, job: dict, req: dict):
    try:
        _update(_renders, render_id, status="rendering")
        start, end = req["start"], req["end"]
        workdir = job_dir(job["id"]) / f"render-{render_id}"
        workdir.mkdir(parents=True, exist_ok=True)
        ass_name = None
        if (req["captions"] and job["chunks"]) or req["hook"].strip():
            ass = captions.build_ass(job["chunks"], start, end, req["aspect"], req["captions"],
                                     req["cap_style"], req["hook"])
            (workdir / "clip.ass").write_text(ass, encoding="utf-8")
            ass_name = "clip.ass"
        graph = media.build_filter(job["width"], job["height"], req["aspect"], req["fill"], req["crop"], ass_name)
        out = media.render_clip(Path(job["source"]), workdir, "clip.mp4", start, end, graph, job["has_audio"],
                                lambda p: _update(_renders, render_id, progress=round(p * 100)))
        _update(_renders, render_id, status="done", progress=100, path=str(out),
                filename=_slug(req.get("title") or "clip") + ".mp4")
    except media.MediaError as exc:
        _update(_renders, render_id, status="error", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("render %s failed", render_id)
        _update(_renders, render_id, status="error", error="Interner Fehler: " + str(exc)[:200])


# ---------- Cleanup ----------

def cleanup_once():
    cutoff = time.time() - settings.job_ttl_hours * 3600
    with _lock:
        old = [j for j, v in _jobs.items() if v["created"] < cutoff and v["status"] in ("ready", "error")]
        for j in old:
            _jobs.pop(j, None)
        for r in [r for r, v in _renders.items() if v["job_id"] in old]:
            _renders.pop(r, None)
        known = set(_jobs)
    for j in old:
        shutil.rmtree(job_dir(j), ignore_errors=True)
    # Folders left over from before a restart.
    if jobs_root().exists():
        for d in jobs_root().iterdir():
            if d.is_dir() and d.name not in known and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)


def cleanup_loop():
    while True:
        cleanup_once()
        time.sleep(1800)
