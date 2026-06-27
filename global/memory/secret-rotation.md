---
tier: global
type: preference
---
# Keep secret/key rotation to ≤2 CLI calls

Pick the shortest recipe for the target's architecture:

- **File-based projects:** one call —
  `gcloud iam service-accounts keys create gcp-key.json --iam-account=<sa>`.
- **Secret-Manager-backed projects:** two calls —
  `firebase functions:secrets:set <NAME> --data-file <file>` then deploy.

- If a rotation needs more than three steps, ask whether the recipe is wrong before
  executing.
- **Never** `re.sub` on the value side of `.env.local` — Python interprets `\n` in the
  replacement string as a newline and corrupts JSON values containing `\n` escapes. Use
  file copy or `str.replace`.
- Don't delete the downloaded key file until the new key is verified in prod.
