"""External agent-CLI runtime adapter — hand the whole task to an external agent.

This is a *runtime* adapter, not an LLM provider. Where the LLM providers
(claude_code/claude_api/ollama/openai) own the model call, this hands the task
to a separate agent runtime that owns its own LLM, tools, and loop. It still
implements the Provider protocol so the router can dispatch uniformly.

It models a generic external agent CLI invoked one turn at a time:

    agentcli agent --message "<prompt>" --json [--agent <id>] [--model <id>]

The command runs ONE agent turn and, with `--json`, prints a JSON object whose
`reply` field holds the agent's response text. We do NOT pass any deliver flag,
so the reply is returned to us only — nothing is sent to any chat channel.
`--model` is forwarded as a hint; the external runtime owns the actual LLM.

There's no `--system-prompt` flag, so the agent's system prompt (from AgentOS
frontmatter) is prepended to the message as a leading block.

billed_to is "external": cost is owned by the external runtime / its provider,
not metered here, so cost_usd stays 0 and AgentOS budgets don't double-count.

Point AGENTCLI_BIN at your own external agent CLI to use this adapter.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from agentos.providers.base import DispatchResult, Provider, ProviderError

AGENTCLI_BIN = os.environ.get("AGENTCLI_BIN", "agentcli")


class AgentCliRuntimeProvider:
    name = "agentcli"

    def __init__(self, binary: str | None = None, timeout: int = 600):
        self.binary = binary or AGENTCLI_BIN
        self.timeout = timeout

    def supports_model(self, model: str) -> bool:
        # The external runtime owns model selection; accept anything as a hint.
        return True

    def _build_message(self, system_prompt: str, user_message: str) -> str:
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
        message = self._build_message(system_prompt, user_message)
        cmd = [binary, "agent", "--json", "--message", message]
        if model:
            cmd.extend(["--model", model])

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
                f"Agent CLI not found ({self.binary}). Is it installed and on "
                "PATH? Set AGENTCLI_BIN to override.",
                retryable=False,
            ) from e
        except subprocess.TimeoutExpired as e:
            raise ProviderError(
                f"Agent CLI turn timed out after {self.timeout}s.",
                retryable=True,
            ) from e

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise ProviderError(
                f"Agent CLI turn failed (exit {proc.returncode}): {detail}",
                retryable=True,
            )

        text = self._extract_text(proc.stdout)

        return DispatchResult(
            text=text,
            model=model,
            provider=self.name,
            cost_usd=0.0,           # external runtime owns the spend
            billed_to="external",
            raw={"stdout": proc.stdout},
        )

    @staticmethod
    def _extract_text(stdout: str) -> str:
        """Pull the reply text out of `agentcli agent --json` output.

        The JSON object carries the agent's response in `reply`; we fall back to
        other common field names and finally to the raw stdout so a JSON-shape
        change across CLI versions degrades gracefully instead of crashing.
        """
        raw = (stdout or "").strip()
        if not raw:
            return ""
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return raw
        if isinstance(data, dict):
            for key in ("reply", "text", "response", "message", "output"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
            return raw
        if isinstance(data, str):
            return data
        return raw


def get_provider() -> Provider:
    return AgentCliRuntimeProvider()
