"""Claude Code provider — runs inference through your Max/Pro subscription.

Instead of calling the metered Anthropic API, this shells out to the locally
installed `claude` CLI in headless mode (`claude -p`). That CLI is authenticated
with your Claude subscription (OAuth, stored in the macOS keychain), so calls are
covered by your flat monthly fee — NO per-token API billing.

Trade-offs vs. the API provider:
  + No per-token charges — uses what you already pay for
  + Same models (sonnet/opus/haiku)
  - Subject to your subscription's rate limits (not dollar caps)
  - Slightly slower (spawns a CLI process per call)
  - Requires the orchestrator to run where it can read the keychain
    (i.e. launched from your normal login session, not a locked-down daemon)

cost_usd is populated with the API-equivalent value (the CLI's own total_cost_usd,
falling back to the pricing table) so budget tracking and the dashboard reflect real
usage; billed_to="subscription" marks that you are not actually charged per call.
subscription_equivalent_usd carries the same figure for "is my subscription worth it"
reporting.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from agentos.providers.base import DispatchResult, Provider, ProviderError
from agentos.providers.pricing import cost_usd as compute_cost

# agentos model aliases → Claude Code model aliases
_MODEL_ALIAS = {
    "claude-opus": "opus",
    "claude-sonnet": "sonnet",
    "claude-haiku": "haiku",
}


def _to_cli_model(model: str) -> str:
    for prefix, alias in _MODEL_ALIAS.items():
        if model.startswith(prefix):
            return alias
    return model  # pass through full names / unknown aliases


class ClaudeCodeProvider:
    name = "claude_code"

    def __init__(self, binary: str = "claude", timeout: int = 300,
                 permission_mode: str | None = None,
                 allowed_tools: list[str] | None = None):
        self.binary = binary
        self.timeout = timeout
        # Headless `claude -p` can't answer permission prompts — without a
        # permission mode, any file edit silently dies at the prompt (bug #4).
        self.permission_mode = permission_mode
        # Tools pre-approved beyond the permission mode (e.g. WebSearch/WebFetch
        # for researcher tasks — acceptEdits alone leaves them prompt-blocked).
        self.allowed_tools = allowed_tools or []

    def supports_model(self, model: str) -> bool:
        return model.startswith("claude")

    def _check_available(self) -> None:
        if shutil.which(self.binary) is None:
            raise ProviderError(
                f"'{self.binary}' CLI not found on PATH. Install Claude Code, or "
                "switch default_provider to 'claude_api' in settings.yaml.",
                retryable=False,
            )

    def dispatch(
        self,
        *,
        model: str,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,  # not used by CLI; kept for interface parity
        max_tokens: int = 8192,    # not used by CLI
        workdir: str | None = None,
    ) -> DispatchResult:
        self._check_available()
        cli_model = _to_cli_model(model)

        cmd = [
            self.binary,
            "-p", user_message,
            "--model", cli_model,
            "--output-format", "json",
        ]
        if system_prompt:
            cmd += ["--system-prompt", system_prompt]
        if self.permission_mode:
            cmd += ["--permission-mode", self.permission_mode]
        if self.allowed_tools:
            cmd += ["--allowedTools", ",".join(self.allowed_tools)]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=workdir,
            )
        except subprocess.TimeoutExpired as e:
            raise ProviderError(
                f"Claude Code timed out after {self.timeout}s", retryable=True
            ) from e
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"Failed to run Claude Code: {e}") from e

        if proc.returncode != 0:
            stderr = (proc.stderr or proc.stdout or "").strip()
            retryable = "rate" in stderr.lower() or "overloaded" in stderr.lower()
            if "not logged in" in stderr.lower() or "login" in stderr.lower():
                raise ProviderError(
                    "Claude Code is not logged in. Run `claude` once interactively "
                    "and `/login` with your Max/Pro account, then retry.",
                    retryable=False,
                )
            raise ProviderError(f"Claude Code error: {stderr}", retryable=retryable)

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise ProviderError(
                f"Could not parse Claude Code output: {e}\n{proc.stdout[:500]}"
            ) from e

        if data.get("is_error"):
            msg = str(data.get("result", ""))
            if "not logged in" in msg.lower() or "/login" in msg.lower():
                raise ProviderError(
                    "Claude Code is not logged in. In a normal terminal run "
                    "`claude` then `/login` with your Max/Pro account, then retry. "
                    "(Or switch default_provider to 'claude_api' in settings.yaml.)",
                    retryable=False,
                )
            retryable = "rate" in msg.lower() or "overloaded" in msg.lower()
            raise ProviderError(f"Claude Code reported error: {msg}", retryable=retryable)

        usage = data.get("usage", {})
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_write = usage.get("cache_creation_input_tokens", 0) or 0
        api_equiv = float(data.get("total_cost_usd", 0.0) or 0.0)
        # populate cost_usd so budget tracking + the dashboard reflect real usage value;
        # prefer the CLI's own total_cost_usd, fall back to the pricing table.
        run_cost = api_equiv if api_equiv > 0 else compute_cost(model, in_tok, out_tok, cache_write, cache_read)

        return DispatchResult(
            text=data.get("result", ""),
            model=model,
            provider=self.name,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=run_cost,  # API-equivalent; billed_to marks it subscription-covered
            subscription_equivalent_usd=api_equiv,
            billed_to="subscription",
            stop_reason=data.get("subtype"),
            raw=data,
        )


def get_provider() -> Provider:
    from agentos.core.config import settings

    orch = settings().get("orchestrator", {})
    timeout = int(orch.get("dispatch_timeout_seconds", 300))
    return ClaudeCodeProvider(
        timeout=timeout,
        permission_mode=orch.get("dispatch_permission_mode"),
        allowed_tools=orch.get("dispatch_allowed_tools"),
    )
