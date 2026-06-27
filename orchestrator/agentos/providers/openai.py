"""OpenAI provider — stub. Implemented in a later phase.

The interface is ready; flipping config to use gpt-4o will work once this is
filled in with the openai SDK. Kept as a stub to prove the model-agnostic seam.
"""

from __future__ import annotations

from agentos.providers.base import DispatchResult, Provider, ProviderError


class OpenAIProvider:
    name = "openai"

    def supports_model(self, model: str) -> bool:
        return model.startswith("gpt")

    def dispatch(self, **kwargs) -> DispatchResult:
        raise ProviderError(
            "OpenAI provider not yet implemented. Use a claude-* model for now.",
            retryable=False,
        )


def get_provider() -> Provider:
    return OpenAIProvider()
