"""Clip selection: Claude reads the transcript, with a loudness-based fallback."""

import json
import logging
import re

import numpy as np

from .media import ENERGY_STEP

log = logging.getLogger("schnittwerk")

CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "score": {"type": "integer"},
                    "reason": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["start", "end", "title", "hook", "score", "reason", "hashtags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}


class ClaudeUnavailableError(Exception):
    pass


def text_between(segments: list[dict], a: float, b: float) -> str:
    return " ".join(s["text"] for s in segments if s["end"] > a and s["start"] < b).strip()


def snap_to_segments(clip: dict, segments: list[dict], window: float = 4.0) -> dict:
    """Move clip borders onto nearby sentence boundaries."""
    if not segments:
        return clip
    starts = [s["start"] for s in segments if abs(s["start"] - clip["start"]) < window]
    if starts:
        clip["start"] = min(starts, key=lambda t: abs(t - clip["start"]))
    ends = [s["end"] for s in segments if abs(s["end"] - clip["end"]) < window and s["end"] > clip["start"] + 5]
    if ends:
        clip["end"] = min(ends, key=lambda t: abs(t - clip["end"]))
    return clip


def sanitize(raw: list[dict], duration: float, min_len: float = 3.0) -> list[dict]:
    """Clamp, validate and de-overlap clips from any source; sorted by score."""
    out: list[dict] = []
    for r in raw:
        try:
            s, e = float(r["start"]), float(r["end"])
        except (KeyError, TypeError, ValueError):
            continue
        s, e = max(0.0, min(s, duration)), max(0.0, min(e, duration))
        if e - s < min_len:
            continue
        if any(s < c["end"] and e > c["start"] for c in out):
            continue
        out.append({
            "start": round(s, 2),
            "end": round(e, 2),
            "title": str(r.get("title") or "Clip")[:80],
            "hook": str(r.get("hook") or "")[:60],
            "score": max(0, min(100, int(r.get("score") or 50))),
            "reason": str(r.get("reason") or "")[:240],
            "hashtags": [str(t)[:30] for t in (r.get("hashtags") or [])][:6],
            "crop": 0.5,
        })
    out.sort(key=lambda c: -c["score"])
    return out


def heuristic(energy: list[float], segments: list[dict], duration: float,
              min_len: float, max_len: float, count: int) -> list[dict]:
    """Pick windows with high loudness, dynamics and little silence."""
    length = min((min_len + max_len) / 2, duration)
    clips: list[dict] = []
    if len(energy) < 4:
        k = max(1, min(count, int(duration // length)))
        for i in range(k):
            st = duration / k * i + max(0.0, (duration / k - length) / 2)
            clips.append({"start": st, "end": min(duration, st + length), "score": 50,
                          "reason": "Gleichmäßig verteilt, da die Tonspur nicht gelesen werden konnte."})
    else:
        e = np.asarray(energy, dtype=np.float64)
        w = max(2, int(round(length / ENERGY_STEP)))
        w = min(w, len(e))
        floor = float(np.percentile(e, 20)) * 1.6 + 0.02
        pre = np.concatenate([[0], np.cumsum(e)])
        pre2 = np.concatenate([[0], np.cumsum(e * e)])
        act = np.concatenate([[0], np.cumsum(e > floor)])
        idx = np.arange(0, len(e) - w + 1, 2)
        mean = (pre[idx + w] - pre[idx]) / w
        sd = np.sqrt(np.maximum(0, (pre2[idx + w] - pre2[idx]) / w - mean ** 2))
        active = (act[idx + w] - act[idx]) / w

        def norm(v):
            m = v.max()
            return v / m if m > 0 else v

        mn, sdn, actn = norm(mean), norm(sd), norm(active)
        score = 0.45 * mn + 0.35 * sdn + 0.2 * actn
        order = np.argsort(-score)
        top = float(score[order[0]]) or 1.0
        for o in order:
            if len(clips) >= count:
                break
            i = int(idx[o])
            s, en = i * ENERGY_STEP, (i + w) * ENERGY_STEP
            if any(s < c["end"] + 2 and en > c["start"] - 2 for c in clips):
                continue
            lo, hi = max(0, i - 4), min(len(e) - 1, i + 2)
            quiet = lo + int(np.argmin(e[lo:hi + 1]))
            s = quiet * ENERGY_STEP + ENERGY_STEP  # start right after the nearest pause
            en = min(duration, max(en, s + w * ENERGY_STEP))
            reason = ("Starke Dynamik in der Stimme, oft ein Zeichen für Pointen oder Betonung."
                      if sdn[o] >= mn[o] else "Durchgehend energiegeladene Passage mit wenig Pausen.")
            clips.append({"start": s, "end": en, "score": round(55 + 41 * float(score[o]) / top), "reason": reason})

    result = []
    for i, c in enumerate(clips):
        snap_to_segments(c, segments)
        txt = text_between(segments, c["start"], c["end"])
        first = re.split(r"(?<=[.!?])\s+", txt)[0] if txt else ""
        c["title"] = first[:60] if first else f"Moment {i + 1}"
        c["hook"] = ""
        c["hashtags"] = []
        result.append(c)
    return sanitize(result, duration)


def build_prompt(segments: list[dict], duration: float, min_len: int, max_len: int, count: int) -> str:
    transcript = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments)
    return (
        "Du bist Editor für Kurzvideos (TikTok, Instagram Reels, YouTube Shorts). Unten steht das "
        f"Transkript eines Videos mit {round(duration)} Sekunden Länge, Zeitstempel in Sekunden.\n\n"
        f"Finde die {count} stärksten, in sich geschlossenen Momente für Kurzclips.\n"
        f"- Jeder Clip ist {min_len} bis {max_len} Sekunden lang.\n"
        "- Er beginnt am Anfang eines Satzes mit einem starken Einstieg und endet nach einem "
        "abgeschlossenen Gedanken. Ohne Vorwissen aus dem restlichen Video verständlich.\n"
        "- Clips überlappen sich nicht. Nutze nur Zeitstempel aus dem Transkript.\n"
        "- title: höchstens 60 Zeichen, in der Sprache des Transkripts.\n"
        "- hook: höchstens 7 Wörter, Text-Overlay für die ersten Sekunden, macht neugierig, "
        "in der Sprache des Transkripts.\n"
        "- score: 0 bis 100, Potenzial für Reichweite. Sei streng, nicht jeder Clip ist über 80.\n"
        "- reason: ein Satz auf Deutsch, warum der Moment funktioniert.\n"
        "- hashtags: 3 bis 5 passende Hashtags.\n"
        "Wenn das Video weniger gute Momente hergibt, liefere weniger Clips.\n\n"
        f"Transkript:\n{transcript}"
    )


def with_claude(segments: list[dict], duration: float, min_len: int, max_len: int, count: int,
                model: str) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": CLIP_SCHEMA}},
            messages=[{"role": "user", "content": build_prompt(segments, duration, min_len, max_len, count)}],
        )
    except anthropic.AuthenticationError as exc:
        raise ClaudeUnavailableError("Der Anthropic-API-Schlüssel ist ungültig.") from exc
    except anthropic.RateLimitError as exc:
        raise ClaudeUnavailableError("Claude ist gerade ausgelastet (Rate-Limit).") from exc
    except anthropic.APIStatusError as exc:
        raise ClaudeUnavailableError(f"Claude-API-Fehler {exc.status_code}.") from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeUnavailableError("Claude ist nicht erreichbar.") from exc

    if response.stop_reason == "refusal":
        raise ClaudeUnavailableError("Claude hat die Anfrage abgelehnt.")
    if response.stop_reason == "max_tokens":
        raise ClaudeUnavailableError("Claudes Antwort wurde abgeschnitten.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClaudeUnavailableError("Claudes Antwort war kein gültiges JSON.") from exc
    log.info("claude request %s: %s clips", response._request_id, len(data.get("clips", [])))
    return sanitize(data.get("clips", []), duration)
