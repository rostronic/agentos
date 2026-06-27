"""Ollama provider (mocked HTTP) + explicit provider routing."""

from __future__ import annotations

import io
import json

import pytest

from agentos.providers.base import ProviderError
from agentos.providers.ollama import OllamaProvider


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _mock_urlopen(payload):
    def _open(req, timeout=None):
        return _FakeResp(json.dumps(payload).encode())
    return _open


def test_successful_local_dispatch(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "42"},
        "prompt_eval_count": 30, "eval_count": 5, "done": True, "done_reason": "stop",
    }
    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen(payload))
    p = OllamaProvider()
    r = p.dispatch(model="gemma4:26b", system_prompt="be terse", user_message="2+2*20?")
    assert r.text == "42"
    assert r.cost_usd == 0.0          # local — never charged
    assert r.billed_to == "local"
    assert r.input_tokens == 30
    assert r.output_tokens == 5
    assert r.provider == "ollama"


def test_server_unreachable_is_retryable(monkeypatch):
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    p = OllamaProvider()
    with pytest.raises(ProviderError) as exc:
        p.dispatch(model="gemma4:26b", system_prompt="", user_message="hi")
    assert exc.value.retryable
    assert "ollama serve" in str(exc.value)


def test_ollama_error_field(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen({"error": "model not found"}))
    p = OllamaProvider()
    with pytest.raises(ProviderError, match="model not found"):
        p.dispatch(model="nope", system_prompt="", user_message="hi")


# --- router selection ---
def test_router_explicit_provider_wins(monkeypatch):
    from agentos.core import router
    prov = router._get_provider("claude-sonnet-4-6", provider="ollama")
    assert prov.name == "ollama"


def test_router_infers_ollama_from_model_family():
    from agentos.core import router
    assert router._get_provider("gemma4:26b").name == "ollama"
    assert router._get_provider("llama3.1:70b").name == "ollama"
    assert router._get_provider("mistral:7b").name == "ollama"


def test_router_unknown_provider_raises():
    from agentos.core import router
    with pytest.raises(ProviderError, match="Unknown provider"):
        router._get_provider("x", provider="nonsense")
