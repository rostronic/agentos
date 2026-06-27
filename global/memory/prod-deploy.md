---
tier: global
type: preference
---
# Production deploys: explicit per-deploy approval

Never run a production deploy without explicit approval **for that specific deploy**.
Past approval never carries over to the next one — each is a fresh decision.

- **The only valid authorization is the literal phrase `deploy to prod`**
  (case-insensitive). "go", "yes", "ship it", "👍" are NOT sufficient — treat anything
  else as "not yet." If the reply is close but not exact ("deploy", "yes deploy"), ask
  for the exact phrase.
- **Make the ask unmissable:** give it its own paragraph/section, a clear question
  ("Authorize prod deploy?"), the named change (commit/branch/summary) and what is
  *not* included, and a restatement that you're waiting for the literal phrase. Never
  bury it in a status update; never wrap it in a tappable multiple-choice button.
- Dev / staging / preview deploys need no approval. Pushing to `main` is fine **unless**
  `main` auto-deploys — verify first; if it does, gate the push like a prod deploy.
- A passing regression suite is necessary but **not** sufficient — still ask.

## Deploy mechanism: Firebase Hosting
All deployable projects use **Firebase Hosting**.
- **Prod deploy** (gated by the rule above): `firebase deploy --only hosting` from the
  project's web dir, against its `.firebaserc` default site.
- **Safe test (no approval needed):** `firebase hosting:channel:deploy <name> --expires 1d`
  → a preview-channel URL, never touches prod.
- Projects whose web app is server-rendered (a Next.js/SSR site) additionally need
  `firebase experiments:enable webframeworks` (one-time per CLI install).

> Consolidates the per-deploy-approval, literal-phrase, and unmissable-ask rules.
