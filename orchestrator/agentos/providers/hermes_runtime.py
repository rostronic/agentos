"""Hermes runtime adapter — hand the whole task to the Hermes agent.

Like the agentcli adapter, this is a *runtime* adapter (not an LLM provider):
it hands the task to a separate agent runtime (Hermes) that owns its own LLM,
tools, and loop, while still implementing the Provider protocol so the router
can dispatch uniformly.

Invocation (confirmed via `hermes --help`):

    hermes -z "<prompt>" [-m <model>] [--provider <provider>]

`-z/--oneshot` is Hermes' one-shot mode: it "sends a single prompt and prints
ONLY the final response text to stdout — no banner, no spinner, no tool
previews, no session_id line." So stdout IS the response text. `-m` and
`--provider` are forwarded as hints; Hermes owns the actual LLM. We do NOT use
`hermes send` (which posts to a platform), so nothing is delivered anywhere.

There's no `--system-prompt` flag for one-shot, so the agent's system prompt
(from AgentOS frontmatter) is prepended to the prompt as a leading block.

billed_to is "external": cost is owned by the Hermes runtime / its provider,
not metered here, so cost_usd stays 0 and AgentOS budgets don't double-count.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from agentos.providers.base import DispatchResult, Provider, ProviderError

HERMES_BIN = os.environ.get("HERMES_BIN", "hermes")


class HermesRuntimeProvider:
    name = "hermes"

    def __init__(self, binary: str | None = None, timeout: int = 600):
        self.binary = binary or HERMES_BIN
        self.timeout = timeout

    def supports_model(self, model: str) -> bool:
        # The external runtime owns model selection; accept anything as a hint.
        return True

    def _build_prompt(self, system_prompt: str, user_message: str) -> str:
        if system_prompt:
            return f"{system_prompt}\n\n---\n\n{user_message}"
        return user_message

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
        binary = shutil.which(self.binary) or self.binary
        prompt = self._build_prompt(system_prompt, user_message)
        cmd = [binary, "-z", prompt]
        if model:
            cmd.extend(["-m", model])

        try:
            proc = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as e:
            raise ProviderError(
                f"Hermes CLI not found ({self.binary}). Is hermes installed and "
                "on PATH? Set HERMES_BIN to override.",
                retryable=False,
            ) from e
        except subprocess.TimeoutExpired as e:
            raise ProviderError(
                f"Hermes one-shot turn timed out after {self.timeout}s.",
                retryable=True,
            ) from e

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise ProviderError(
                f"Hermes one-shot turn failed (exit {proc.returncode}): {detail}",
                retryable=True,
            )

        # In `-z` mode stdout IS the final response text.
        text = (proc.stdout or "").strip()

        return DispatchResult(
            text=text,
            model=model,
            provider=self.name,
            cost_usd=0.0,           # external runtime owns the spend
            billed_to="external",
            raw={"stdout": proc.stdout},
        )


def get_provider() -> Provider:
    return HermesRuntimeProvider()
