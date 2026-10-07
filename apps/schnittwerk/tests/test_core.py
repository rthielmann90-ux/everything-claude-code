import pytest
from schnittwerk import captions, download, highlights, media

WORDS = [
    {"w": "Das", "s": 10.0, "e": 10.2}, {"w": "ist", "s": 10.2, "e": 10.4},
    {"w": "der", "s": 10.4, "e": 10.6}, {"w": "Punkt.", "s": 10.6, "e": 11.0},
    {"w": "Danach", "s": 12.5, "e": 12.9}, {"w": "{kommt}", "s": 12.9, "e": 13.3},
]


def test_chunks_respect_word_limit_punctuation_and_gaps():
    chunks = captions.build_chunks(WORDS)
    assert [[w["w"] for w in c["words"]] for c in chunks] == [["Das", "ist", "der"], ["Punkt."], ["Danach", "{kommt}"]]
    # tiny gap between the first two chunks is closed, the 1.5 s pause is kept
    assert chunks[0]["e"] == chunks[1]["s"]
    assert chunks[2]["s"] == 12.5


def test_ass_times_are_relative_and_text_is_escaped():
    chunks = captions.build_chunks(WORDS)
    ass = captions.build_ass(chunks, 10.0, 14.0, "9:16", True, "bold", "Hook {x}")
    assert "PlayResX: 720" in ass and "PlayResY: 1280" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:04.00,Hook,,0,0,0,,Hook (x)" in ass
    assert "DAS IST DER" in ass
    assert "(KOMMT)" in ass and "{kommt}" not in ass


def test_ass_karaoke_highlights_one_word_per_event():
    chunks = captions.build_chunks(WORDS[:3])
    ass = captions.build_ass(chunks, 10.0, 12.0, "9:16", True, "karaoke", "")
    events = [line for line in ass.splitlines() if line.startswith("Dialogue")]
    assert len(events) == 3
    assert all(e.count(captions.ACCENT) == 1 for e in events)


def test_ass_skips_chunks_outside_clip():
    chunks = captions.build_chunks(WORDS)
    ass = captions.build_ass(chunks, 0.0, 5.0, "16:9", True, "bold", "")
    assert "Dialogue" not in ass


def test_filter_crop_landscape_to_vertical():
    graph = media.build_filter(1920, 1080, "9:16", "crop", 0.0, None)
    assert graph.startswith("[0:v]crop=608:1080:0:0,scale=720:1280")
    right = media.build_filter(1920, 1080, "9:16", "crop", 1.0, "clip.ass")
    assert "crop=608:1080:1312:0" in right and right.endswith(",subtitles=clip.ass[v]")


def test_filter_blur_and_vertical_source():
    assert "boxblur" in media.build_filter(1920, 1080, "9:16", "blur", 0.5, None)
    graph = media.build_filter(1080, 1920, "16:9", "crop", 0.5, None)
    assert "crop=1080:608:0:656" in graph


def test_sanitize_clamps_and_drops_overlaps():
    raw = [
        {"start": 10, "end": 50, "title": "A", "score": 90},
        {"start": 40, "end": 70, "title": "overlap", "score": 80},
        {"start": 100, "end": 400, "title": "too long video end", "score": 120},
        {"start": "x", "end": 5},
        {"start": 200, "end": 201},
    ]
    out = highlights.sanitize(raw, duration=150)
    assert [c["title"] for c in out] == ["too long video end", "A"]
    assert out[0]["end"] == 150 and out[0]["score"] == 100


def test_heuristic_finds_loud_section_and_titles_from_transcript():
    energy = [0.05] * 400
    for i in range(200, 280):  # loud, dynamic region at 100-140 s
        energy[i] = 0.9 if i % 3 else 0.4
    segments = [{"start": 99.5, "end": 104.0, "text": "Hier kommt die Pointe. Und mehr."}]
    clips = highlights.heuristic(energy, segments, duration=200, min_len=30, max_len=60, count=2)
    assert clips, "expected at least one clip"
    best = clips[0]
    assert best["start"] <= 100 and best["end"] >= 140  # covers the whole loud section
    assert best["title"] == "Hier kommt die Pointe."


def test_heuristic_without_audio_spreads_clips():
    clips = highlights.heuristic([], [], duration=300, min_len=30, max_len=60, count=3)
    assert len(clips) == 3
    assert all(c["end"] - c["start"] == pytest.approx(45) for c in clips)


@pytest.mark.parametrize("url", ["ftp://example.com/x", "http://127.0.0.1/x", "http://localhost:8000/",
                                 "http://169.254.169.254/latest/meta-data", "not a url"])
def test_validate_url_rejects_unsafe(url):
    with pytest.raises(download.DownloadError):
        download.validate_url(url)


def test_long_caption_gets_smaller_font():
    words = [{"w": "ERSTE", "s": 0, "e": 0.4}, {"w": "WICHTIGE", "s": 0.4, "e": 0.8}, {"w": "SATZ", "s": 0.8, "e": 1.2}]
    ass = captions.build_ass(captions.build_chunks(words, max_chars=40), 0, 2, "9:16", True, "bold", "")
    size = int(ass.split("{\\fs")[1].split("}")[0])
    assert size < 58
    assert captions.fit_size("JA", 58, 604, True) == 58
