---
tier: global
type: preference
---
# Always cite files with absolute paths in chat

In chat responses, link/cite files with **absolute paths**, never relative ones.

**Why:** sessions often run with cwd set to a git worktree, while the deliverable files
live in the project root — a relative markdown link resolves against the worktree and
404s every time.

- Chat-facing link → full absolute path, e.g.
  `~/agentos/projects/<proj>/marketing/x.md` expanded to your home dir.
- Relative links *between files in the same directory* (e.g. a README linking to its
  siblings) are fine — this rule is specifically about links in chat messages.
