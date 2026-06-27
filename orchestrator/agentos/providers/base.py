"""Provider interface — the model-agnostic seam.

Every model backend (Claude, OpenAI, Ollama) implements this interface.
The router picks a provider based on an agent's frontmatter and calls it
uniformly. Swapping models never touches agent prompts or the orchestrator core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class DispatchResult:
    """The normalized result of a single agent dispatch, regardless of provider."""

    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    # API-equivalent cost when running under a subscription (Max/Pro via Claude
    # Code). You are NOT charged this — it's for "value of my subscription"
    # reporting only. cost_usd stays 0 for subscription runs so budgets don't
    # block work you've already paid for via the flat fee.
    subscription_equivalent_usd: float = 0.0
    billed_to: str = "api"  # "api" (per-token) or "subscription" (Max/Pro)
    stop_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Provider(Protocol):
    """A model backend. Implementations live in providers/<name>.py."""

    name: str

    def supports_model(self, model: str) -> bool:
        """Whether this provider can serve the given model alias."""
        ...

    def dispatch(
        self,
        *,
        model: str,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        workdir: str | None = None,
    ) -> DispatchResult:
        """Run one completion. Synchronous; raises ProviderError on failure.

        workdir: working directory for agents that act on a filesystem (the
        CLI-backed providers run their subprocess there, e.g. a task worktree).
        Pure-API providers accept and ignore it.
        """
        ...


class ProviderError(Exception):
    """Raised when a provider fails (auth, rate limit, model unavailable, etc.)."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable
