"""ffprobe/ffmpeg helpers: probing, audio extraction, loudness curve and clip rendering."""

import json
import subprocess
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

OUTPUT_DIMS = {"9:16": (720, 1280), "1:1": (1080, 1080), "16:9": (1280, 720)}
ENERGY_STEP = 0.5  # seconds per loudness sample


class MediaError(Exception):
    pass


def probe(path: Path) -> dict:
    """Return duration, display width/height (rotation applied) and audio presence."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise MediaError("Die Datei ist kein lesbares Video.")
    data = json.loads(out.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise MediaError("Die Datei enthält keine Videospur.")
    width, height = int(video["width"]), int(video["height"])
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(side["rotation"])
    rotation = rotation or int((video.get("tags") or {}).get("rotate", 0) or 0)
    if abs(rotation) % 180 == 90:
        width, height = height, width
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise MediaError("Die Videolänge konnte nicht bestimmt werden.")
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return {"duration": duration, "width": width, "height": height, "has_audio": has_audio}


def extract_audio(src: Path, dst: Path) -> None:
    """Write a 16 kHz mono WAV, the input format Whisper expects."""
    res = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(dst)],
        capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise MediaError("Die Tonspur konnte nicht gelesen werden.")


def loudness(wav_path: Path, step: float = ENERGY_STEP) -> list[float]:
    """RMS per `step` seconds, normalised to 0..1."""
    with wave.open(str(wav_path), "rb") as wf:
        rate = wf.getframerate()
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32)
    win = max(1, int(rate * step))
    n = int(np.ceil(len(samples) / win))
    if n == 0:
        return []
    padded = np.zeros(n * win, dtype=np.float32)
    padded[: len(samples)] = samples
    rms = np.sqrt(np.mean(padded.reshape(n, win) ** 2, axis=1))
    peak = float(rms.max())
    if peak > 0:
        rms = rms / peak
    return [round(float(v), 4) for v in rms]


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def build_filter(src_w: int, src_h: int, aspect: str, fill: str, crop: float, ass_name: str | None) -> str:
    """ffmpeg filter_complex producing [v] in the target format."""
    out_w, out_h = OUTPUT_DIMS[aspect]
    a = out_w / out_h
    crop = min(1.0, max(0.0, crop))
    if src_w / src_h > a:
        ch, cw = _even(src_h), _even(src_h * a)
        cw = min(cw, src_w - src_w % 2)
        cx, cy = int((src_w - cw) * crop), 0
    else:
        cw, ch = _even(src_w), _even(src_w / a)
        ch = min(ch, src_h - src_h % 2)
        cx, cy = 0, int((src_h - ch) * crop)
    if fill == "blur":
        bx, by = (src_w - cw) // 2, (src_h - ch) // 2
        graph = (f"[0:v]split=2[a][b];"
                 f"[a]crop={cw}:{ch}:{bx}:{by},scale={out_w}:{out_h},boxblur=20:2,eq=brightness=-0.15[bg];"
                 f"[b]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease[fg];"
                 f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    else:
        graph = f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale={out_w}:{out_h}:flags=lanczos,setsar=1"
    if ass_name:
        graph += f",subtitles={ass_name}"
    return graph + "[v]"


def render_clip(src: Path, workdir: Path, out_name: str, start: float, end: float, filter_graph: str,
                has_audio: bool, on_progress: Callable[[float], None]) -> Path:
    """Cut and re-encode one clip. Runs with cwd=workdir so the ASS file needs no path escaping."""
    duration = max(0.1, end - start)
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}",
           "-filter_complex", filter_graph, "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", out_name]
    proc = subprocess.Popen(cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                on_progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / duration))
            except ValueError:
                pass
    err = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        raise MediaError("Rendern fehlgeschlagen: " + err.strip()[-300:])
    return workdir / out_name
