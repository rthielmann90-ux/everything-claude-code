import json
from types import SimpleNamespace

import anthropic
import pytest
from schnittwerk import highlights

SEGMENTS = [{"start": 0.0, "end": 40.0, "text": "Satz eins."}, {"start": 40.0, "end": 80.0, "text": "Satz zwei."}]


def fake_client(monkeypatch, response):
    calls = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return response

    client = SimpleNamespace(beta=SimpleNamespace(messages=Messages()))
    monkeypatch.setattr(anthropic, "Anthropic", lambda: client)
    return calls


def test_claude_clips_are_parsed_and_sanitized(monkeypatch):
    payload = {"clips": [
        {"start": 0, "end": 35, "title": "Eins", "hook": "Hör zu", "score": 70, "reason": "r", "hashtags": ["#a"]},
        {"start": 40, "end": 999, "title": "Zwei", "hook": "", "score": 88, "reason": "r", "hashtags": []},
    ]}
    response = SimpleNamespace(stop_reason="end_turn", _request_id="req_test",
                               content=[SimpleNamespace(type="text", text=json.dumps(payload))])
    calls = fake_client(monkeypatch, response)
    clips = highlights.with_claude(SEGMENTS, 80.0, 30, 60, 2, "claude-opus-5-5")
    assert [c["title"] for c in clips] == ["Zwei", "Eins"]
    assert clips[0]["end"] == 80.0
    req = calls[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["fallbacks"] == "default"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert "[40.0-80.0] Satz zwei." in req["messages"][0]["content"]


def test_claude_refusal_raises_unavailable(monkeypatch):
    fake_client(monkeypatch, SimpleNamespace(stop_reason="refusal", content=[], _request_id="req_test"))
    with pytest.raises(highlights.ClaudeUnavailableError):
        highlights.with_claude(SEGMENTS, 80.0, 30, 60, 2, "claude-opus-5-5")
