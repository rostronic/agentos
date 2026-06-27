# Contributing to AgentOS

Thanks for your interest in improving AgentOS! This project is open source under
the Apache-2.0 license. Contributions of all sizes are welcome — bug reports,
docs fixes, new agents/workflows, and core improvements.

## Getting set up

```bash
git clone https://github.com/rostronic/agentos.git
cd agentos
cp config/user.yaml.example config/user.yaml   # then edit it
cd orchestrator
pip install -e ".[dev]"                         # installs the test/lint extras
```

## Before you open a PR

- **Tests pass.** Run the unit suite (no API key needed):
  ```bash
  cd orchestrator
  pytest agentos/tests/ -m "not integration"
  ```
- **Lint is clean.** This project uses [ruff](https://docs.astral.sh/ruff/):
  ```bash
  ruff check orchestrator/agentos
  ```
- **New behavior is covered by a test.** Every bug fix should add a regression
  test so the bug can't silently return — see [BUGS.md](BUGS.md) for the pattern.
- **No secrets or personal data.** Never commit `config/user.yaml`,
  `config/credentials/.env`, real API keys, or anything under your private
  `workspaces/`. The `*.example` templates are the only config files tracked in
  the repo; keep it that way.

## Project conventions

- **Config is templated.** Add new config as `config/<name>.yaml.example`; read it
  through `agentos.core.config` accessors rather than hardcoding values. Personal
  values (name, email, timezone, paths) come from `config/user.yaml`.
- **Memory stays host-neutral.** Anything under `global/memory/` should read
  cleanly for any agent host, not just Claude.
- **Keep changes surgical.** Small, focused PRs with a clear description are easier
  to review and land faster.

## Reporting bugs

Open a GitHub issue with: what you ran, what you expected, what happened, and your
environment (OS, Python version, provider). A minimal repro helps enormously.

## Code of conduct

Be respectful and constructive. We want AgentOS to be a welcoming project for
contributors of every background and experience level.
