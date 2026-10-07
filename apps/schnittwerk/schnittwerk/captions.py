"""Caption chunking and ASS subtitle generation.

Chunks are computed once on the server and sent to the browser, so the live
preview and the rendered file show exactly the same caption groups.
"""

import re

from .media import OUTPUT_DIMS

ACCENT = "&H003CC9FF&"   # #ffc93c in ASS BGR order
WHITE = "&H00FFFFFF&"
INK = "&H0000161D&"      # #1d1600
FONT = "DejaVu Sans"


def build_chunks(words: list[dict], max_words: int = 3, max_chars: int = 16, max_gap: float = 0.6) -> list[dict]:
    """Group timed words ({w, s, e}) into short caption chunks ({s, e, words})."""
    chunks: list[dict] = []
    cur: list[dict] = []

    def flush():
        if cur:
            chunks.append({"s": cur[0]["s"], "e": cur[-1]["e"], "words": list(cur)})
            cur.clear()

    for w in words:
        text = w["w"].strip()
        if not text:
            continue
        if cur and w["s"] - cur[-1]["e"] > max_gap:
            flush()
        cur.append({"w": text, "s": w["s"], "e": w["e"]})
        chars = sum(len(x["w"]) + 1 for x in cur)
        if len(cur) >= max_words or chars > max_chars or re.search(r"[.!?,;:]$", text):
            flush()
    flush()
    # Close tiny gaps so captions don't flicker between chunks.
    for a, b in zip(chunks, chunks[1:]):
        if 0 < b["s"] - a["e"] < 0.3:
            a["e"] = b["s"]
    return chunks


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def fit_size(text: str, font_size: int, max_width: float, bold: bool) -> int:
    """Shrink the font so one caption line fits, like the browser preview does.

    Uses an average glyph width for DejaVu Sans; slightly conservative so the
    line rarely wraps.
    """
    em = 0.0
    for ch in text:
        if ch == " ":
            em += 0.32
        elif ch.isupper() or ch.isdigit():
            em += 0.76 if bold else 0.70
        else:
            em += 0.64 if bold else 0.58
    if em * font_size <= max_width or em == 0:
        return font_size
    return max(10, int(max_width / em))


def _esc(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(chunks: list[dict], clip_start: float, clip_end: float, aspect: str,
              captions: bool, cap_style: str, hook: str, hook_seconds: float = 4.0) -> str:
    """ASS document for one clip; event times are relative to clip_start."""
    w, h = OUTPUT_DIMS[aspect]
    base = min(w, h)
    dur = clip_end - clip_start
    cap_fs = round(base * (0.055 if cap_style == "minimal" else 0.08))
    hook_fs = round(base * 0.058)
    cap_mv = round(h * (0.12 if aspect == "16:9" else 0.26))
    side = round(w * 0.08)

    if cap_style == "minimal":
        cap_style_line = (f"Style: Cap,{FONT},{cap_fs},{WHITE},{WHITE},&H64000000&,&H64000000&,"
                          f"0,0,0,0,100,100,0,0,3,{round(cap_fs * 0.25)},0,2,{side},{side},{cap_mv},1")
    else:
        cap_style_line = (f"Style: Cap,{FONT},{cap_fs},{WHITE},{WHITE},&H00000000&,&H80000000&,"
                          f"-1,0,0,0,100,100,0,0,1,{max(2, round(cap_fs * 0.12))},0,2,{side},{side},{cap_mv},1")
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        cap_style_line,
        f"Style: Hook,{FONT},{hook_fs},{INK},{INK},{ACCENT},{ACCENT},-1,0,0,0,100,100,0,0,3,"
        f"{round(hook_fs * 0.35)},0,8,{round(w * 0.11)},{round(w * 0.11)},{round(h * 0.09)},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def event(style: str, s: float, e: float, text: str):
        s, e = max(0.0, s - clip_start), min(dur, e - clip_start)
        if e - s >= 0.02:
            lines.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},{style},,0,0,0,,{text}")

    if hook.strip():
        event("Hook", clip_start, clip_start + min(hook_seconds, dur), _esc(hook.strip()))

    if captions:
        for ck in chunks:
            if ck["e"] <= clip_start or ck["s"] >= clip_end:
                continue
            words = [_esc(x["w"]) for x in ck["words"]]
            if cap_style != "minimal":
                words = [x.upper() for x in words]
            size = fit_size(" ".join(words), cap_fs, w - 2 * side, cap_style != "minimal")
            prefix = f"{{\\fs{size}}}" if size != cap_fs else ""
            if cap_style == "karaoke":
                for i, wd in enumerate(ck["words"]):
                    s = ck["s"] if i == 0 else wd["s"]
                    e = ck["words"][i + 1]["s"] if i + 1 < len(ck["words"]) else ck["e"]
                    text = " ".join(
                        f"{{\\1c{ACCENT}}}{t}{{\\1c{WHITE}}}" if j == i else t for j, t in enumerate(words))
                    event("Cap", s, e, prefix + text)
            else:
                event("Cap", ck["s"], ck["e"], prefix + " ".join(words))
    return "\n".join(lines) + "\n"
