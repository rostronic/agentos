"""Claude provider — wraps the Anthropic Python SDK."""

from __future__ import annotations

from agentos.core.config import get_api_key
from agentos.providers.base import DispatchResult, Provider, ProviderError
from agentos.providers.pricing import cost_usd


class ClaudeProvider:
    """Anthropic backend. Honors model alias from agent frontmatter."""

    name = "claude"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or get_api_key("claude")
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not self._api_key:
                raise ProviderError(
                    "ANTHROPIC_API_KEY not set. Add it to "
                    "~/agentos/config/credentials/.env",
                    retryable=False,
                )
            try:
                import anthropic
            except ImportError as e:
                raise ProviderError(f"anthropic SDK not installed: {e}") from e
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def supports_model(self, model: str) -> bool:
        return model.startswith("claude")

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
        client = self._get_client()
        try:
            resp = client.messages.create(
                model=model,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:  # noqa: BLE001 — normalize all SDK errors
            retryable = "rate_limit" in str(e).lower() or "overloaded" in str(e).lower()
            raise ProviderError(f"Claude dispatch failed: {e}", retryable=retryable) from e

        text = "".join(block.text for block in resp.content if hasattr(block, "text"))
        usage = resp.usage
        in_tok = getattr(usage, "input_tokens", 0)
        out_tok = getattr(usage, "output_tokens", 0)
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

        return DispatchResult(
            text=text,
            model=model,
            provider=self.name,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=cost_usd(model, in_tok, out_tok, cache_write, cache_read),
            stop_reason=getattr(resp, "stop_reason", None),
        )


def get_provider() -> Provider:
    return ClaudeProvider()
