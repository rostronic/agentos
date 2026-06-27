# FAQ

Short answers to the questions that come up in the first hour with AgentOS. For the
command walkthrough see [USAGE.md](USAGE.md); for the architecture see
[DESIGN.md](DESIGN.md).

---

### 1. A dispatch failed with a `401` — what happened?

Your provider auth lapsed. On the default `claude_code` provider, AgentOS runs on
your Claude Max/Pro **subscription** via the `claude` CLI, and that login expires
periodically. Fix it by re-authenticating once:

```bash
claude        # then type: /login
```

Then re-run the dispatch — no AgentOS config change is needed. If you're on the
metered `claude_api` provider instead, a `401` means a missing or invalid
`ANTHROPIC_API_KEY`; check `config/credentials/.env`.

### 2. The first dispatch was slow / felt like it hung. Is that normal?

The **first** run on a fresh machine has one-time cost that later runs don't:

- On `claude_code`, the very first call may prompt an interactive login if you
  haven't run `claude` + `/login` yet. Do that one-time step in a normal terminal
  first (see Q1) so dispatches run unattended.
- The orchestrator initializes its local sqlite cache and loads agent specs +
  memory tiers on first use.
- The model itself takes a few seconds for a real research/coding task — that's the
  agent working, not a hang. Watch progress live with `agentos serve` (the run
  streams into the dashboard over SSE) or check `agentos runs` after.

If a run genuinely stalls, `agentos pause` is the kill switch, and `agentos runs`
shows the last run's status and error.

### 3. Which provider should I choose?

Set `default_provider` in `config/settings.yaml` (or pick it during
`agentos init`):

| Provider | Best for | Cost |
|---|---|---|
| **`claude_code`** (default) | Daily use on a Claude **Max/Pro** plan | No per-token charge — uses your subscription (rate-limited, not dollar-capped) |
| `claude_api` | Headless servers / CI with no interactive login | Metered Anthropic API, pay per token (`ANTHROPIC_API_KEY`) |
| `ollama` | Kicking the tires locally with no account | Free, local — exercises the orchestration plumbing, not production-quality output |

Most people want `claude_code`. Use `claude_api` where there's no interactive
session to `/login`, and `ollama` only to test the plumbing without a key.

### 4. Where do I put context about a specific project?

In that **project's own `memory/`** directory — so it loads only when an agent is
dispatched at that project, and never leaks into a sibling project. Facts that are
true across a whole sphere go in `workspaces/<personal|business>/memory/`; facts
true everywhere go in `global/memory/`. The
[memory quickstart](memory-quickstart.md) walks through adding a fact and proving
it loaded; [workspaces.md](workspaces.md) explains the full tier model. To bring an
existing project's docs in automatically, use
`agentos onboard <slug> --curate` ([onboarding.md](onboarding.md)).

### 5. The dashboard is empty — did something break?

No. `agentos serve` reads your local run history, and a fresh install has none yet.
The home view shows a welcome banner with the exact commands to populate it.
Dispatch one agent (`agentos dispatch researcher "find 3 sources on X"`) or run a
workflow and the KPIs, Runs, and Live feed fill in immediately. The dashboard binds
to `127.0.0.1` only — nothing leaves your machine.

### 6. Does running AgentOS cost money?

On the default `claude_code` provider, **no per-token charge** — dispatches run on
your existing Max/Pro subscription (subject to your plan's rate limits). The
**daily briefing** (`agentos brief`) and the dashboard make **zero** model calls —
they're computed from local files and a no-auth public weather API. Only
`claude_api` is metered. Either way, `config/budgets.yaml` enforces `daily_usd` /
`per_run_usd` caps and `agentos budget` shows today's usage.

### 7. How do I run the tests / try it without touching anything real?

```bash
cd orchestrator
pip install -e ".[dev]"

# Fast unit tests — no API key, no cost, no network
pytest agentos/tests/ -m "not integration"
```

To exercise an end-to-end agent dispatch cheaply, point `default_provider` at
`ollama` (free, local) or run the shipped demo in
[`examples/example-project/`](../examples/example-project/) — see the
[projects quickstart](projects-quickstart.md). The integration tests (real model
calls) are opt-in: `AGENTOS_RUN_INTEGRATION=1 pytest -m integration`.
