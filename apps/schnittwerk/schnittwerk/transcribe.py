"""Speech recognition with faster-whisper (local, no API key)."""

import threading
from collections.abc import Callable
from pathlib import Path

from .config import settings

_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(settings.whisper_model, device=settings.whisper_device,
                                  compute_type=settings.whisper_compute)
        return _model


def transcribe(wav: Path, duration: float, language: str | None,
               on_progress: Callable[[float], None]) -> tuple[list[dict], list[dict], str]:
    """Returns (segments, words, detected_language)."""
    model = _get_model()
    seg_iter, info = model.transcribe(str(wav), language=language or None, word_timestamps=True, vad_filter=True)
    segments: list[dict] = []
    words: list[dict] = []
    for seg in seg_iter:
        text = seg.text.strip()
        if text:
            segments.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": text})
        for w in seg.words or []:
            if w.word.strip():
                words.append({"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)})
        if duration > 0:
            on_progress(min(1.0, seg.end / duration))
    return segments, words, info.language
