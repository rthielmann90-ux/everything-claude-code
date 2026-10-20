import json

from llm.core.types import LLMInput, Message, Role
from llm.providers.ollama import (
    DEFAULT_OLLAMA_CLOUD_MODEL,
    OLLAMA_CLOUD_URL,
    OLLAMA_LOCAL_URL,
    OllamaProvider,
)


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps({"message": {"content": "ok"}, "done_reason": "stop"}).encode()


def _clear(monkeypatch):
    for name in ("OLLAMA_API_KEY", "OLLAMA_BASE_URL", "OLLAMA_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_ollama_defaults_to_local_without_key(monkeypatch):
    _clear(monkeypatch)
    provider = OllamaProvider()
    assert provider.base_url == OLLAMA_LOCAL_URL
    assert provider.default_model == "llama3.2"


def test_ollama_uses_cloud_with_key(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OLLAMA_API_KEY", "secret")
    provider = OllamaProvider()
    assert provider.base_url == OLLAMA_CLOUD_URL
    assert provider.default_model == DEFAULT_OLLAMA_CLOUD_MODEL


def test_ollama_sends_bearer_header_only_with_key(monkeypatch):
    _clear(monkeypatch)
    sent = []
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout: sent.append(req) or _Response()
    )
    llm_input = LLMInput(messages=[Message(role=Role.USER, content="hi")])

    OllamaProvider(api_key="secret").generate(llm_input)
    OllamaProvider().generate(llm_input)

    assert sent[0].full_url == f"{OLLAMA_CLOUD_URL}/api/chat"
    assert sent[0].get_header("Authorization") == "Bearer secret"
    assert sent[1].full_url == f"{OLLAMA_LOCAL_URL}/api/chat"
    assert sent[1].get_header("Authorization") is None
