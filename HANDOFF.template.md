<!--
AgentOS session-handoff template.

A handoff is a chat-only, end-of-session summary that lets a fresh agent (or you,
tomorrow) pick up exactly where this session left off — without re-deriving the
context. Copy this file to HANDOFF.md, fill in each section, and delete any that
don't apply. Keep it terse and concrete: decisions, what shipped, key files
(ABSOLUTE paths), running state, how to verify, and what's still open.
-->

# Session Handoff — <one-line title of what this session was about>

_Written <YYYY-MM-DD>. Audience: the next agent, ideally a session rooted at `~/agentos`._

## Where it started
<!-- The original ask and any framing the next agent needs. 1–3 sentences. -->

## Decisions locked + what shipped
<!-- Bullet the concrete outcomes. For each: what changed, the absolute file path(s),
     and the commit/branch if applicable. -->
-

## Key files for next session
<!-- Absolute paths to the files that matter, grouped by feature/area. -->
-

## Running state
<!-- Background processes (with how to stop them), dev servers / ports, open
     worktrees or branches, and anything STALE that needs a restart/refresh. -->
- Background processes:
- Dev servers / ports:
- Open worktrees / branches:

## Verification — how to confirm things still work
<!-- The exact commands to run and what a healthy result looks like. -->
-

## Deferred + open questions
<!-- Work intentionally not done, blockers, and questions awaiting the user. -->
-

## Pick up here
<!-- The single most useful next action for whoever resumes. -->
