---
tier: global
type: preference
---
# Code-repo edits stay in the session worktree

For a project that is its own git repo, treat the **parent checkout as read-only**
(Read/grep fine, never Edit/Write). Make every edit, commit, and branch operation
inside the session's worktree.

**Why:** the parent checkout is the user's active human workspace — often on an
unrelated branch with dirty WIP and modified secret files (e.g. `gcp-key.json`).
Writing there entangles agent work with theirs, creates duplicate commits, and risks
committing unreviewed secrets.

- If a needed file isn't visible in the worktree, rebase the worktree onto `origin/main`
  (or create a fresh one) rather than falling back to the parent checkout.
- Run dev servers / scripts from inside the worktree so artifacts (`.next/`, reports,
  `.env.local` mutations) stay scoped.
- **Exception:** a flat assets/marketing folder with no `.git` of its own — writing to
  the project root is fine and expected; the worktree concern doesn't apply.
