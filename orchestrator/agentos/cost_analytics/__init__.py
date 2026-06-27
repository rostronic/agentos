"""Cross-source per-project cost tracking (Phase 1 MVP).

Tracks total real-dollar cost per project across every spend source — Claude
(API-equivalent), Google Cloud / Firebase, and third-party APIs — attributed to
registry-slug projects with an explicit "unmapped" bucket. Distinct from
token_analytics (Claude API-equivalent only). See docs/cost-tracking-plan.md.
"""
