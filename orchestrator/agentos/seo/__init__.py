"""Weekly SEO digest reader (read-only, LOCAL files only).

Joins each managed site's latest `SEO_REVIEW_<date>.md` (human digest) with its
`findings_<date>.json` (structured actionable / watch issues) under the project's
`docs/seo/reviews/` dir. Repo paths come from the project registry (projects.yaml),
so this stays in sync with onboarding. Observes only — never writes, never touches
the network. Powers the dashboard SEO panel and the digest-summary notification.
"""
