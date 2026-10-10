#!/usr/bin/env python3
"""
Generate macOS launchd plists for project cron jobs from each project repo's
`cron/jobs.json` (the source of truth). One plist per ENABLED job:

    config/launchd/com.agentos.cron.<job-name-with-hyphens>.plist

(label uses hyphens, e.g. blog_daily_digest -> com.agentos.cron.blog-daily-digest,
so regenerating replaces rather than duplicates them).

Which repos: listed in `config/scheduler-repos.json` (a JSON list of paths, `~` allowed;
see `config/scheduler-repos.json.example`), or the `AGENTOS_SCHEDULER_REPOS` environment
variable (paths separated by ':'). Each repo needs a `cron/jobs.json` with
`{"jobs": [{"name", "cron", "wrapper", "enabled"}]}`.

Each plist runs the repo's existing wrapper through run_job_quiet.sh (silent on
success, macOS notification + email on failure, throttled). SEO-digest jobs use
run_job.sh so the weekly digest email keeps going out. Wrapper output lands in
<repo>/cron/logs/<log>.log; launchd stdout/stderr in ~/agentos/logs/launchd/.

Cron -> StartCalendarInterval (evaluated in the Mac's local time zone):
fields support '*', 'N', 'a,b,c', 'a-b', '*/n', 'a-b/n'; the cartesian product
of the restricted fields becomes an array of calendar dicts (so '0 */4 * * *'
fires at 00,04,...,20 aligned to the hour rather than StartInterval drift).

Usage:
    python3 gen_plists.py            # write plists
    python3 gen_plists.py --print    # show job -> schedule summary only
    python3 gen_plists.py --install  # also copy into ~/Library/LaunchAgents,
                                     # `launchctl enable` + (re)bootstrap in gui/$UID
    python3 gen_plists.py --install --only blog-daily-digest,site-qa-smoke   # subset
LLM calls inside the jobs use the `claude` CLI (Max subscription); the plists
never set ANTHROPIC_API_KEY and the wrappers unset it.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
AGENTOS = HOME / "agentos"
OUT_DIR = AGENTOS / "config" / "launchd"
LAUNCH_AGENTS = HOME / "Library" / "LaunchAgents"
RUNNER_DIR = AGENTOS / "scheduler" / "launchd"
LOG_DIR = AGENTOS / "logs" / "launchd"
REPOS_CONFIG = AGENTOS / "config" / "scheduler-repos.json"


def _load_repos() -> list[Path]:
    """Project repos whose cron/jobs.json to schedule (env var wins over the config file)."""
    env = os.environ.get("AGENTOS_SCHEDULER_REPOS", "").strip()
    if env:
        raw = [p for p in env.split(":") if p]
    elif REPOS_CONFIG.exists():
        raw = json.loads(REPOS_CONFIG.read_text())
    else:
        sys.exit(f"No repos configured: create {REPOS_CONFIG} (see {REPOS_CONFIG.name}.example) "
                 "or set AGENTOS_SCHEDULER_REPOS")
    return [Path(os.path.expanduser(str(p))) for p in raw]


REPOS = _load_repos()
PATH = (f"{HOME}/.local/bin:/opt/homebrew/bin:/opt/homebrew/opt/node@24/bin:"
        "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")

# cron field -> (launchd key, min, max)
FIELDS = [("Minute", 0, 59), ("Hour", 0, 23), ("Day", 1, 31), ("Month", 1, 12), ("Weekday", 0, 7)]


def _expand(field: str, lo: int, hi: int) -> list[int] | None:
    """Return the sorted values for one cron field, or None for '*' (any)."""
    if field == "*":
        return None
    vals: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
            if step != 1:
                b = hi
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise ValueError(f"cron field {field!r} out of range {lo}-{hi}")
        vals.update(range(a, b + 1, step))
    return sorted(vals)


def cron_to_calendar(expr: str) -> dict | list[dict]:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"expected 5 cron fields: {expr!r}")
    if parts[2] != "*" and parts[4] != "*":
        # cron ORs day-of-month and day-of-week; launchd ANDs them. Refuse.
        raise ValueError(f"both day-of-month and day-of-week set (unsupported): {expr!r}")
    keyed = []
    for (key, lo, hi), raw in zip(FIELDS, parts):
        vals = _expand(raw, lo, hi)
        if vals is not None:
            if key == "Weekday":
                vals = sorted({0 if v == 7 else v for v in vals})
            keyed.append((key, vals))
    if not keyed:
        raise ValueError(f"every-minute schedules not supported: {expr!r}")
    combos = [dict(zip([k for k, _ in keyed], c)) for c in itertools.product(*[v for _, v in keyed])]
    return combos[0] if len(combos) == 1 else combos


def label_for(job: str) -> str:
    return "com.agentos.cron." + job.replace("_", "-")


def log_basename(wrapper: str) -> str:
    name = Path(wrapper).name
    if name.startswith("run_"):
        name = name[4:]
    return name[:-3] if name.endswith(".sh") else name


def build_plist(job: dict, project_dir: str) -> dict:
    name = job["name"]
    runner = "run_job.sh" if "seo" in name else "run_job_quiet.sh"
    return {
        "Label": label_for(name),
        "ProgramArguments": [
            "/bin/bash", str(RUNNER_DIR / runner),
            name, project_dir, job["wrapper"], log_basename(job["wrapper"]),
        ],
        "StartCalendarInterval": cron_to_calendar(job["cron"]),
        "EnvironmentVariables": {"PATH": PATH, "HOME": str(HOME), "LANG": "en_US.UTF-8"},
        "WorkingDirectory": project_dir,
        "StandardOutPath": str(LOG_DIR / f"{name}.out"),
        "StandardErrorPath": str(LOG_DIR / f"{name}.err"),
        "RunAtLoad": False,
        "ProcessType": "Background",
    }


def describe(cal) -> str:
    cals = cal if isinstance(cal, list) else [cal]
    if len(cals) == 1:
        return " ".join(f"{k}={v}" for k, v in cals[0].items())
    return f"{len(cals)} slots, first {cals[0]}, last {cals[-1]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--print", action="store_true", help="summary only; write nothing")
    ap.add_argument("--install", action="store_true", help="copy to ~/Library/LaunchAgents + bootstrap")
    ap.add_argument("--only", default="", help="comma list of labels/job names to install (default all)")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    uid = os.getuid()
    written: list[Path] = []
    for repo in REPOS:
        spec = json.loads((repo / "cron" / "jobs.json").read_text())
        project_dir = spec.get("projectDir") or str(repo)
        for job in spec["jobs"]:
            if not job.get("enabled", False):
                print(f"skip (disabled)  {job['name']}")
                continue
            if not (Path(project_dir) / job["wrapper"]).is_file():
                print(f"ERROR missing wrapper {project_dir}/{job['wrapper']}", file=sys.stderr)
                return 1
            pl = build_plist(job, project_dir)
            print(f"{pl['Label']:<45} {job['cron']:<16} {describe(pl['StartCalendarInterval'])}")
            if args.print:
                continue
            path = OUT_DIR / f"{pl['Label']}.plist"
            with path.open("wb") as fh:
                plistlib.dump(pl, fh, sort_keys=True)
            written.append(path)

    if args.install:
        only = {o.strip().replace("_", "-").replace("com.agentos.cron.", "")
                for o in args.only.split(",") if o.strip()}
        for path in written:
            if only and path.stem.replace("com.agentos.cron.", "") not in only:
                continue
            dest = LAUNCH_AGENTS / path.name
            label = path.stem
            subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                           capture_output=True)
            shutil.copy2(path, dest)
            # A label may have been `launchctl disable`d earlier; clear that
            # override or bootstrap refuses ("Service is disabled").
            subprocess.run(["launchctl", "enable", f"gui/{uid}/{label}"], capture_output=True)
            r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(dest)],
                               capture_output=True, text=True)
            print(f"{'loaded' if r.returncode == 0 else 'LOAD FAILED'}  {label} {r.stderr.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
