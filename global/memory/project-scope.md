---
tier: global
type: preference
---
# Stay inside the invoked project

When a session is scoped to one project, restrict **everything** — reads, edits,
builds, deploys, suggestions, observations — to that project's folder. Don't read,
edit, propose changes to, or even discuss sibling projects unless explicitly asked.

- Filter `git log` / `git status` / `gh pr list` to the active project's paths; don't
  surface sibling-project changes.
- Don't enumerate other projects' infra (Firebase projects, etc.) unless directly
  relevant.
- Don't propose cross-project cleanups ("I noticed your other project also needs X").
- **Exception:** if a sibling genuinely blocks the active project (shared scripts/
  config), surface it briefly and ask before acting.

> The agentos tier model enforces this **structurally**: a session loads only its own
> project + workspace + global memory, so sibling memory was never in context. This
> rule remains the behavioral backstop. Consolidates the former "stay in project" and
> "project scope" rules.
