"""Ollama provider — local models, zero API cost.

Talks to a locally-running Ollama server (default http://localhost:11434) via
its native /api/chat endpoint. No API key, no per-token billing — inference runs
on your machine. cost_usd is always 0; billed_to is "local".

Models are Ollama tags, e.g. "gemma4:26b", "llama3.1:70b". Because those names
don't follow the claude-*/gpt-* convention the router infers from, an agent
selects this provider explicitly (frontmatter `provider: ollama`) or via the
CLI `--provider ollama` flag.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from agentos.providers.base import DispatchResult, Provider, ProviderError

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


class OllamaProvider:
    name = "ollama"

    def __init__(self, host: str | None = None, timeout: int = 600):
        self.host = (host or OLLAMA_HOST).rstrip("/")
        self.timeout = timeout

    def supports_model(self, model: str) -> bool:
        # Ollama can serve anything that's pulled; the router selects us
        # explicitly, so accept any non-claude/non-gpt tag.
        return not model.startswith(("claude", "gpt"))

    def dispatch(
        self,
        *,
        model: str,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        workdir: str | None = None,  # interface parity; not used by this provider
    ) -> DispatchResult:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise ProviderError(
                f"Could not reach Ollama at {self.host} ({e}). "
                "Is it running? Try: ollama serve",
                retryable=True,
            ) from e
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"Ollama dispatch failed: {e}") from e

        if data.get("error"):
            raise ProviderError(f"Ollama error: {data['error']}")

        text = (data.get("message") or {}).get("content", "")
        in_tok = data.get("prompt_eval_count", 0) or 0
        out_tok = data.get("eval_count", 0) or 0

        return DispatchResult(
            text=text,
            model=model,
            provider=self.name,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=0.0,          # local — no charge
            billed_to="local",
            stop_reason=data.get("done_reason"),
            raw=data,
        )


def get_provider() -> Provider:
    return OllamaProvider()
