# GCP Deployment Cookbook — running AgentOS cron jobs on Google Cloud

Patterns for moving a project's scheduled jobs off a laptop and onto GCP-native
infrastructure (Cloud Scheduler + Cloud Run), so they run reliably without your
machine being awake. This is a recipe book, not a migration log — pick the pattern
that fits each job.

All identifiers below are placeholders. Substitute your own:

| Placeholder | Meaning |
|---|---|
| `my-project-prod` | your GCP **project id** |
| `us-central1` | your **region** (pick one close to your data/users) |
| `job-runner@my-project-prod.iam.gserviceaccount.com` | a **service account** for the job |
| `scheduler-invoker@my-project-prod.iam.gserviceaccount.com` | the SA Cloud Scheduler uses to invoke a Job |
| `cron-jobs` | an Artifact Registry repo name |
| `https://www.example.com/` | your live site URL |

> Prerequisites: a GCP project with billing enabled, the `gcloud` CLI authenticated
> (`gcloud auth login` + `gcloud config set project my-project-prod`), and the APIs
> enabled (`gcloud services enable run.googleapis.com cloudscheduler.googleapis.com
> artifactregistry.googleapis.com cloudbuild.googleapis.com`).

---

## Two patterns, pick per job

| Pattern | For | Trigger | Runs in |
|---|---|---|---|
| **A — ping a URL** | warmup, smoke checks | Cloud Scheduler → HTTP GET | nothing new (hits the live site) |
| **B — run a script** | content/enrichment/batch jobs | Cloud Scheduler → Cloud Run **Job** | a container (batch, runs to completion) |

```
                 ┌── Pattern A: warmup, smoke ──────────────┐
 Cloud Scheduler ┤  HTTP GET → live site / /api/warmup       │
  (cron + TZ)    └───────────────────────────────────────────┘
                 ┌── Pattern B: content jobs ───────────────┐
 Cloud Scheduler ┤  POST :run → Cloud Run Job (container) ───┼─▶ Datastore/Firestore
  (cron + TZ)    │     • reads Secret Manager for env        ├─▶ Cloud Logging
                 │     • workload identity (no key file)     │
                 └───────────────────────────────────────────┘
```

Key distinction: **Cloud Run *Service*** = a web app (serves HTTP, scales to zero).
**Cloud Run *Job*** = a batch container for cron scripts (no HTTP, runs to completion).

---

## Pattern A — the warmup/smoke slice (proves Cloud Scheduler, zero risk)

### Stage 0 — trivial win, ~5 min, no code

The lowest-risk way to prove Cloud Scheduler works: a periodic GET that keeps a
scale-to-zero Service warm and removes any laptop dependency for basic uptime checks.

```bash
gcloud scheduler jobs create http site-warmup \
  --location=us-central1 \
  --schedule="*/5 * * * *" \
  --time-zone="America/Los_Angeles" \
  --uri="https://www.example.com/" \
  --http-method=GET
```

That single GET every 5 min keeps the homepage's Cloud Run instance warm. Cloud
Scheduler is now proven; retire the laptop's warmup once confirmed.

### Stage 1 — richer warmup (multi-page / data-ranked sample)

If you need to warm more than the homepage (e.g. a ranked sample of detail pages),
prefer an **app endpoint over a separate container**:

- **Recommended: a `/api/warmup` route** on the app. Scheduler hits one URL; the route
  fans out server-side to all the paths you want warm (it already has data access for
  any ranked picks). No new container/image, and the warming request lands on the live
  Service — exactly the instance you want hot.
  ```bash
  --uri="https://www.example.com/api/warmup"
  ```
- **Alternative: containerize a warmup script** as a Cloud Run Job (Pattern B). Less
  app rewrite, but a job cold-starts on every tick — wasteful vs an endpoint if it
  runs frequently.

**Gotcha:** to warm a CDN edge, the warmup must hit the **public URL**
(`https://www.example.com/<path>`). A server fetching `localhost` only warms itself,
not the edge.

---

## Pattern B — content/batch jobs (Cloud Run Job)

### 1. One Artifact Registry repo

```bash
gcloud artifacts repositories create cron-jobs \
  --repository-format=docker --location=us-central1
```

### 2. One image per project (not per job)

Build a single image and pick the entrypoint per Job via `--command/--args` — far
cheaper to maintain than one image per script.

```dockerfile
# Dockerfile.cron
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# no fixed CMD — each Cloud Run Job sets its own command/args
```
```bash
gcloud builds submit \
  --tag us-central1-docker.pkg.dev/my-project-prod/cron-jobs/app:latest
```

### 3. A Cloud Run Job per script (same image, different command)

```bash
gcloud run jobs create my-batch-job \
  --image=us-central1-docker.pkg.dev/my-project-prod/cron-jobs/app:latest \
  --region=us-central1 \
  --command=python --args="scripts/my_script.py,--limit,200,--sleep,0.2" \
  --service-account=job-runner@my-project-prod.iam.gserviceaccount.com \
  --set-secrets="ANTHROPIC_API_KEY=anthropic-key:latest" \
  --max-retries=1 --task-timeout=1800
```

### 4. Schedule it (Scheduler → Run Job)

The newer shorthand wires the schedule and the Job together:

```bash
gcloud run jobs deploy my-batch-job \
  --image=...  --region=us-central1 \
  --schedule="30 5 * * *"   # creates the Cloud Scheduler trigger for you
```

Or explicit, for more control over time zone / auth:

```bash
gcloud scheduler jobs create http my-batch-job-trigger \
  --location=us-central1 \
  --schedule="30 5 * * *" --time-zone="America/Los_Angeles" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/my-project-prod/jobs/my-batch-job:run" \
  --http-method=POST \
  --oauth-service-account-email=scheduler-invoker@my-project-prod.iam.gserviceaccount.com
```

---

## IAM / secrets (workload identity instead of key files)

Prefer workload identity over a downloaded service-account key file — there's no key
to rotate or leak, and `google.auth.default()` picks up the Job's identity
automatically.

- **Job SA** (`job-runner@my-project-prod`): grant only the data roles the script needs
  (e.g. `roles/datastore.user` for Firestore). No key file.
- **Scheduler invoker SA** (`scheduler-invoker@my-project-prod`): grant
  `roles/run.invoker` so it can `:run` the Job.
- **Secrets** (`ANTHROPIC_API_KEY`, third-party API keys, …): store in Secret Manager
  and reference via `--set-secrets` on the Job — drop any committed `.env.local`.

```bash
gcloud secrets create anthropic-key --replication-policy=automatic
printf '%s' "$ANTHROPIC_API_KEY" | gcloud secrets versions add anthropic-key --data-file=-
gcloud secrets add-iam-policy-binding anthropic-key \
  --member="serviceAccount:job-runner@my-project-prod.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

---

## Stateful jobs — Cloud Run Jobs have no persistent disk

Cloud Run Jobs start from a clean container every run, so any state a script reads or
writes between runs (dedup sets, "seen" maps, rotation counters) must live in a
managed store, not a local file.

| State shape | Example | Recommended target |
|---|---|---|
| Tiny counter | rotation index, "last run" marker | Firestore doc (atomic) or a small GCS blob |
| Small set | a short allow/seen list | Firestore doc |
| Growing dedup map | "seen URLs / seen items", grows forever | **Firestore, one doc per item (hashed id + timestamp), with a native TTL policy** |

**Why one-doc-per-item + TTL for the big dedup sets** (rather than a single GCS blob):

- A GCS blob forces a **read-modify-write of the whole blob** every run. If two runs
  overlap (e.g. a high-frequency schedule), they clobber each other → lost entries →
  re-processed / duplicate work. Firestore gives **point-read dedup and per-doc writes**
  (no clobber).
- The blob grows forever; a Firestore **TTL policy** auto-expires old docs (e.g. >60
  days) so the set stays bounded.

**Ship-fast fallback:** if concurrency isn't a concern yet and the state file is small,
a single GCS blob is fine as a first cut — refactor to Firestore-TTL the first time
overlapping runs cause a lost update. Just know that bill is coming if you scale the
schedule up.

---

## Schedule control patterns

- **Pause/resume around events.** Create a second, higher-frequency Scheduler job for a
  surge window **paused**, and have a tiny weekly function flip it with
  `gcloud scheduler jobs resume/pause` around the event. Keeps steady-state cheap while
  handling bursts.
- **Time zones.** Always set `--time-zone` explicitly (e.g.
  `--time-zone="America/Los_Angeles"`); the cron expression is interpreted in that zone
  and DST is handled for you.

---

## Migration order (lowest risk first)

1. **Stage-0 warmup** (Pattern A) — Scheduler HTTP, no container. Instant win, proves
   Scheduler.
2. **One content Job end-to-end** — pick the simplest, idempotent, stateless script and
   prove the full Pattern B: image → Job → Scheduler → datastore → Cloud Logging.
3. **Migrate state to Firestore** (per the table above), then the stateful jobs.
4. **Remaining jobs**, swapping any key-file auth for workload identity.
5. **Event-window automation** (pause/resume) if you have surge schedules.
6. **Keep the laptop schedule as a fallback for ~1 week per job**, then disable it.

---

## Cost (infra only; Claude/API usage is separate)

Rough order-of-magnitude for a handful of small daily jobs:

- **Cloud Scheduler:** ~$0.10/job/mo (first 3 jobs free).
- **Cloud Run Jobs:** ~$1–5/mo total (you pay for the minutes each job actually runs).
- **Warmup as Scheduler HTTP:** effectively free (no `minInstances`, no always-on
  instance).
- **Firestore / Logging / Secret Manager:** ~$0–5/mo at low volume.
- **Typical new infra total: ~$5–15/mo.** Measure yours — see
  `docs/cost-analytics-design.md` for attributing this spend per project in the Cost
  dashboard.

---

## Design decisions worth keeping in mind

### Don't duplicate app logic in a cron container

If a warmup or batch script re-implements ranking/sorting/business logic the app
already has, containerizing it means maintaining **two copies** that silently drift.
Prefer an **app endpoint** (`/api/warmup`, an internal job route) that reuses the app's
own functions — single source of truth, no second container. Reserve a standalone
container for genuinely app-independent work.

### Hit public URLs to warm the edge

A warmup that fetches `localhost` only warms the instance it runs on. To warm a CDN
edge, request the **public URL**.

### Workload identity over key files

Downloaded service-account JSON keys are a rotation and leak liability. On GCP-native
infra you almost never need them — grant the Job's service account the roles it needs
and let `google.auth.default()` use the ambient identity.
