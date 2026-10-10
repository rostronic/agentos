# AgentOS scheduler (macOS launchd)

Run each project's scheduled jobs on your Mac with launchd: no cron daemon, no cloud
scheduler, no always-on server. Jobs fire on schedule, survive reboots and missed wake-ups,
log to files, and alert you only when something fails.

## Pieces

| File | What it does |
|---|---|
| `gen_plists.py` | Reads each project repo's `cron/jobs.json` and writes one launchd plist per enabled job (`config/launchd/com.agentos.cron.<job>.plist`). `--install` loads them. |
| `run_job_quiet.sh` | Default runner. Silent on success; on failure, a macOS notification plus an email, throttled to one per job per 6 hours. |
| `run_job.sh` | Runner that notifies on every run (for weekly digests and similar). |
| `wait_for_network.sh` | Sourced by both runners. If the Mac is offline (asleep, travelling), it rechecks every 15 minutes up to 4 times, then skips quietly instead of failing and emailing. |
| `../notify.sh` | Email / notification helper the runners call. |

## Set up

1. Give each project a `cron/jobs.json`:

   ```json
   {
     "jobs": [
       { "name": "blog_daily_digest", "cron": "30 6 * * *", "wrapper": "cron/run_daily_digest.sh", "enabled": true }
     ]
   }
   ```

   `wrapper` is a script in the repo that does the work. Cron fields support `*`, `N`,
   `a,b`, `a-b`, `*/n` and `a-b/n`, evaluated in the Mac's local time zone.

2. List the repos in `config/scheduler-repos.json` (copy `config/scheduler-repos.json.example`):

   ```json
   ["~/dev/my-site", "~/dev/another-project"]
   ```

   or set `AGENTOS_SCHEDULER_REPOS=~/dev/my-site:~/dev/another-project`.

3. Generate and load:

   ```bash
   python3 scheduler/launchd/gen_plists.py --print     # preview job -> schedule
   python3 scheduler/launchd/gen_plists.py --install   # write + load into launchd
   ```

## Operate

- List: `launchctl list | grep com.agentos.cron`
- Run one now: `launchctl kickstart gui/$(id -u)/com.agentos.cron.<job>`
- Logs: `<repo>/cron/logs/<job>.log`; launchd stdout/stderr and a one-line-per-run
  `runner.log` in `~/agentos/logs/launchd/`.

## Notes

- Jobs run only while the Mac is awake; launchd runs a missed job once on wake.
  `wait_for_network.sh` keeps those wake-up runs from failing when there's no connection.
- If a job calls Claude headlessly (`claude -p`), pass
  `--settings config/claude/headless-settings.json` so interactive-only mods can't stall it
  (see `mods/README.md`).
- Never put API keys in plists or wrappers. Use your Claude subscription through the
  `claude` CLI, and strip `ANTHROPIC_API_KEY` in wrappers if you want to be sure it isn't used.
