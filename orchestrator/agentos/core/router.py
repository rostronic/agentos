"""Router — dispatches an agent on a task via the right provider.

This is the heart of Phase 1. It:
  1. Loads the agent spec (system prompt + model + params from frontmatter)
  2. Checks the budget
  3. Picks a provider for the model (with fallback chain)
  4. Records the run in sqlite (start → success/failure)
  5. Records spend
"""

from __future__ import annotations

from dataclasses import dataclass

from agentos.core import budget, memory_context, run_store
from agentos.core.agent_loader import get_agent
from agentos.providers.base import DispatchResult, Provider, ProviderError


@dataclass
class DispatchOutcome:
    """What the router returns to the caller (CLI, MCP, workflow runner)."""

    ok: bool
    run_id: str
    text: str = ""
    error: str = ""
    blocked_reason: str = ""
    cost_usd: float = 0.0
    model: str = ""
    billed_to: str = "api"  # "subscription" when run via Claude Code / Max plan


# Provider registry — picks the backend for a model.
# An explicit provider (agent frontmatter `provider:` or CLI `--provider`) wins;
# otherwise the backend is inferred from the model name.
#   claude_code (default) → Max/Pro subscription via the CLI (no API $)
#   claude_api            → metered Anthropic API (needs ANTHROPIC_API_KEY)
#   ollama                → local models, $0 (e.g. gemma4:26b, llama3.1:70b)
#   openai                → stub
def _provider_by_name(name: str) -> Provider:
    if name in ("claude_code", "claude"):
        from agentos.providers.claude_code import get_provider
        return get_provider()
    if name == "claude_api":
        from agentos.providers.claude import get_provider
        return get_provider()
    if name == "ollama":
        from agentos.providers.ollama import get_provider
        return get_provider()
    if name == "openai":
        from agentos.providers.openai import get_provider
        return get_provider()
    raise ProviderError(f"Unknown provider: {name}", retryable=False)


# Runtime registry — selects WHO executes the agent (the "runtime" axis).
# Orthogonal to the LLM-provider axis above:
#   native             → the AgentOS orchestrator runs the agent itself, picking
#                        an LLM backend via _get_provider() (current behavior).
#   agentcli / hermes  → hand the whole task to that external agent runtime,
#                        which owns its own LLM. These adapters implement the
#                        same Provider protocol, so dispatch() stays uniform.
def _runtime_provider(name: str) -> Provider:
    if name == "agentcli":
        from agentos.providers.agentcli_runtime import get_provider
        return get_provider()
    if name == "hermes":
        from agentos.providers.hermes_runtime import get_provider
        return get_provider()
    raise ProviderError(f"Unknown runtime: {name}", retryable=False)


def _get_provider(model: str, provider: str | None = None) -> Provider:
    if provider:
        return _provider_by_name(provider)
    if model.startswith("claude"):
        from agentos.core.config import settings
        backend = settings().get("orchestrator", {}).get("default_provider", "claude_code")
        return _provider_by_name(backend)
    if model.startswith("gpt"):
        from agentos.providers.openai import get_provider  # stub
        return get_provider()
    if model.startswith(("llama", "ollama", "gemma", "mistral", "qwen", "phi")):
        from agentos.providers.ollama import get_provider
        return get_provider()
    # Default to the configured Claude backend
    from agentos.providers.claude_code import get_provider
    return get_provider()


def _model_chain(agent: dict) -> list[str]:
    """Ordered list of models to try: preferred first, then fallbacks."""
    model_spec = agent.get("model")
    if isinstance(model_spec, str):
        return [model_spec]
    if isinstance(model_spec, dict):
        chain = [model_spec.get("preferred")]
        chain.extend(model_spec.get("fallback", []))
        return [m for m in chain if m]
    return ["claude-sonnet-4-6"]


def dispatch(
    agent_name: str,
    task: str,
    *,
    model_override: str | None = None,
    provider_override: str | None = None,
    runtime_override: str | None = None,
    project: str | None = None,
    triggered_by: str = "cli",
    task_id: str | None = None,
    workdir: str | None = None,
) -> DispatchOutcome:
    """Dispatch a single agent on a task. Synchronous.

    The agent is addressed along three axes: runtime : provider : model.

    runtime_override (or the agent's frontmatter `runtime:`, default "native")
    picks WHO executes the agent: "native" runs it in this orchestrator (and the
    provider/model axes apply as below); "agentcli"/"hermes" hand the whole task
    to that external agent runtime, which owns its own LLM (provider/model become
    hints).

    provider_override (or the agent's frontmatter `provider:`) forces a specific
    LLM backend (claude_code / claude_api / ollama / openai); otherwise it's
    inferred from the model name. Only used on the native runtime.
    """
    agent = get_agent(agent_name)
    if agent is None:
        return DispatchOutcome(ok=False, run_id="", error=f"Unknown agent: {agent_name}")

    provider_name = provider_override or agent.get("provider")
    runtime_name = runtime_override or agent.get("runtime", "native")

    # Pre-flight budget check
    block = budget.check_dispatch(project=project)
    if block:
        run = run_store.Run(
            agent=agent_name, status="blocked", inputs={"task": task},
            triggered_by=triggered_by, task_id=task_id, project=project,
            error=block.detail,
        )
        run.ended_at = run.started_at
        run_store.create_run(run)
        return DispatchOutcome(
            ok=False, run_id=run.id, blocked_reason=block.reason, error=block.detail
        )

    # Start the run record
    models = [model_override] if model_override else _model_chain(agent)
    run = run_store.Run(
        agent=agent_name, status="running", inputs={"task": task},
        triggered_by=triggered_by, task_id=task_id, project=project,
        model=models[0],
    )
    run_store.create_run(run)
    run_store.append_event(run.id, "dispatch_start", {"agent": agent_name, "task": task})

    system_prompt = agent.get("system_prompt", "")
    temperature = float(agent.get("temperature", 0.3))
    max_tokens = int(agent.get("max_tokens", 8192))

    # --- Layered memory injection (deterministic; must never break a dispatch) ---
    # Native runtimes let the orchestrator own the system prompt → prepend memory
    # there. agentcli/hermes own their own LLM/prompt → fold memory into the
    # user message instead. When there's no memory, this is a no-op (identical to
    # pre-memory behavior).
    is_native = not (runtime_name and runtime_name != "native")
    effective_system_prompt = system_prompt
    effective_user_message = task
    try:
        mem = memory_context.build_context(agent_name, project=project, task=task)
    except Exception:  # noqa: BLE001 — memory is best-effort; never fail a dispatch
        mem = ""
    if mem:
        if is_native:
            effective_system_prompt = f"{mem}\n\n{system_prompt}" if system_prompt else mem
        else:
            effective_user_message = f"{mem}\n\n---\n\n{task}"
        run_store.append_event(
            run.id,
            "memory_injected",
            {
                "chars": len(mem),
                "project": project,
                "target": "system" if is_native else "message",
            },
        )

    # Try each model in the chain until one succeeds.
    # Surface the PREFERRED model's error (first in chain) as the primary one —
    # it's the root cause the user most likely cares about, not a downstream stub.
    primary_error = ""
    for model in models:
        try:
            if runtime_name and runtime_name != "native":
                provider = _runtime_provider(runtime_name)
            else:
                provider = _get_provider(model, provider_name)
            result: DispatchResult = provider.dispatch(
                model=model,
                system_prompt=effective_system_prompt,
                user_message=effective_user_message,
                temperature=temperature,
                max_tokens=max_tokens,
                workdir=workdir,
            )
        except ProviderError as e:
            if not primary_error:
                primary_error = str(e)
            run_store.append_event(run.id, "provider_error", {"model": model, "error": str(e)})
            continue  # try next model in chain
        except Exception as e:  # noqa: BLE001
            if not primary_error:
                primary_error = str(e)
            run_store.append_event(run.id, "error", {"model": model, "error": str(e)})
            continue

        # Success
        budget.record_spend(result.cost_usd, project=project)
        # Notify if this spend crossed a budget threshold (best-effort).
        try:
            crossed = budget.threshold_crossed(project=project)
            if crossed:
                from agentos.core.config import budget_for_project
                from agentos.notify import notifier
                cap = budget_for_project(project).get("daily_usd", 0)
                notifier.budget_threshold(crossed, budget.today_spend(), cap)
        except Exception:  # noqa: BLE001 — never break a dispatch on notify failure
            pass
        run_store.update_run(
            run.id,
            status="done",
            ended_at=run_store._now(),
            output=result.text,
            model=result.model,
            cost_tokens=result.total_tokens,
            cost_usd=result.cost_usd,
        )
        run_store.append_event(
            run.id, "dispatch_done",
            {"model": result.model, "tokens": result.total_tokens, "cost_usd": result.cost_usd},
        )
        return DispatchOutcome(
            ok=True, run_id=run.id, text=result.text,
            cost_usd=result.cost_usd, model=result.model,
            billed_to=result.billed_to,
        )

    # All models failed
    run_store.update_run(run.id, status="failed", ended_at=run_store._now(), error=primary_error)
    return DispatchOutcome(ok=False, run_id=run.id, error=primary_error or "All models failed")
