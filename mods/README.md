# Mods

Claude Code **mods** (Claude Code 2.1.287+) are small add-ons that run inside Claude Code
and change what it shows and how it behaves: a band above the prompt, a side pane, a
slash command, or a hook that holds a tool call. They run in the Claude Code terminal and
the desktop app's Code tab. In headless `claude -p` runs their hooks still fire, but
nothing is drawn.

AgentOS doesn't vendor other people's mods. It publishes a **marketplace** that pins each
recommended mod to a reviewed commit:

```bash
claude plugin marketplace add <owner>/agentos
claude plugin install token-weather@agentos-mods --scope user
```

## Catalog

| Mod | Author | What it does | Notes |
|---|---|---|---|
| `you-should-know` | Anthropic (built in) | A side agent watches longer tasks and posts a note above the prompt when it spots something you might miss. | Off by default. Turn on with `claude plugin enable cc-plugin-you-should-know@builtin`. It uses extra plan usage, so disable it for headless runs. |
| `agents-md` | Anthropic (built in) | Loads `AGENTS.md` as project instructions where a project has no `CLAUDE.md`. | On by default. AgentOS's provider-neutral memory relies on it; leave it on. |
| `diff` | Anthropic (built in) | `/diff` shows the session's uncommitted changes, file by file. | On by default. Useful before a commit or a deploy ask. |
| `token-weather` | Anthropic sample | Context-window fill, tokens and a sparkline above the prompt. | Read-only. |
| `replay-theater` | Anthropic sample | `/replay` steps through each edit from the last turn. | Read-only; never blocks. |
| `blast-radius` | Anthropic sample | Holds risky shell commands (`rm -rf`, `git reset --hard`, force push, `git clean`…) with Proceed/Cancel and a list of what would change. | **Interactive only.** See below. |
| `savvy-progress` | Third party (JohnnyVizz, MIT) | Live subagent panel: model, progress, estimated usage. `/agents-info`. | Young project; its usage figure is an estimate. |
| `skins` | Third party (hellosverre, MIT) | Seven themes for what Claude Code draws. `/skin dracula`. | Cosmetic. Makes a small update-check request. |

Anthropic samples come from
[anthropics/claude-code-playground](https://github.com/anthropics/claude-code-playground/tree/main/claude-code/mods)
(Apache-2.0, shared as-is). Third-party entries are labelled and pinned by SHA. Read a
mod's source before installing it. Mods are **not sandboxed**: they run with your
permissions. `claude plugin validate <dir>` lists the events a mod hooks and the calls it
makes.

## Headless runs (cron, dispatch)

A user-scope mod also loads into scheduled `claude -p` jobs. A mod that **holds** a tool
call waiting for a button press will stall such a job. `blast-radius` waits up to 10
minutes and then denies. AgentOS turns interactive-only mods off per run with
`config/claude/headless-settings.json`:

```bash
claude -p "$PROMPT" --settings "$HOME/agentos/config/claude/headless-settings.json"
```

The orchestrator's Claude Code provider and the scheduler runners pass this file
automatically. Any new headless call site should pass it too.

## Rules for adding a mod here

1. Pin by commit SHA; record who wrote it and its license.
2. Anything that holds a tool call must be listed as interactive-only and added to
   `config/claude/headless-settings.json`.
3. No personal paths, accounts or project names in AgentOS-authored mods. Read them
   from the session (`$.session.cwd()`) or config.
