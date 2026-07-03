"""Readiness/health check — is this AgentOS instance correctly set up and operational?

`agentos doctor` runs these checks and renders an aligned checklist. The framework
already survived one laptop-swap setup-drift; this is the insurance that catches the
next one early. Pure logic here (returns structured results); cli.py renders + sets
the exit code, so the whole thing is unit-testable against a temp AGENTOS_ROOT.

Everything resolves through ``agentos.core.config`` at call time (never hardcode
``Path.home()/"agentos"`` — that was a real CI bug). Tests point a temp root by
monkeypatching ``config.AGENTOS_ROOT`` / ``config.CONFIG_DIR`` / ``config.ENV_FILE``,
so we read those attributes dynamically rather than importing them once.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agentos.core import config

# Status levels, worst-last so we can pick the most severe with max().
PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
_SEVERITY = {PASS: 0, WARN: 1, FAIL: 2}

# Minimum interpreter the package supports (pyproject: requires-python >=3.11).
MIN_PYTHON = (3, 11)


@dataclass
class Check:
    """One health check's outcome."""

    name: str
    status: str  # PASS | WARN | FAIL
    detail: str = ""
    fix: str = ""  # one-line fix hint, shown when status != PASS


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", fix: str = "") -> Check:
        c = Check(name=name, status=status, detail=detail, fix=fix)
        self.checks.append(c)
        return c

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.status == PASS)

    @property
    def warnings(self) -> int:
        return sum(1 for c in self.checks if c.status == WARN)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if c.status == FAIL)

    @property
    def ok(self) -> bool:
        """True when nothing FAILed (warnings are fine)."""
        return self.failed == 0


def _valid_yaml(path: Path) -> tuple[bool, str]:
    """(ok, error message). Empty file is valid YAML (parses to None)."""
    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
        return True, ""
    except yaml.YAMLError as e:
        return False, str(e).splitlines()[0] if str(e) else "invalid YAML"


# --- individual checks ------------------------------------------------------
# Each takes the report and appends one or more Check rows. They read config
# attributes live (config.AGENTOS_ROOT etc.) so a monkeypatched temp root is honored.


def _check_root(report: DoctorReport) -> Path:
    root = config.AGENTOS_ROOT
    if root.is_dir():
        report.add("AGENTOS_ROOT", PASS, str(root))
    else:
        report.add(
            "AGENTOS_ROOT", FAIL, str(root),
            fix="Clone the framework here or set $AGENTOS_ROOT to your checkout.",
        )
    return root


def _check_package(report: DoctorReport) -> None:
    pyver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    try:
        import agentos

        ver = getattr(agentos, "__version__", "?")
        report.add("Package: agentos importable", PASS, f"version {ver}")
    except Exception as e:  # noqa: BLE001
        report.add(
            "Package: agentos importable", FAIL, str(e),
            fix="Install the package: uv pip install -e orchestrator (or pip install -e).",
        )

    if sys.version_info[:2] >= MIN_PYTHON:
        report.add("Package: Python version", PASS, f"{pyver}")
    else:
        report.add(
            "Package: Python version", FAIL, f"{pyver}",
            fix=f"Use Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} (project requires it).",
        )


def _check_config_files(report: DoctorReport) -> None:
    cdir = config.CONFIG_DIR
    # settings/budgets/projects: required + must parse.
    for name in ("settings.yaml", "budgets.yaml", "projects.yaml"):
        path = cdir / name
        if not path.exists():
            report.add(
                f"Config: {name}", FAIL, "missing",
                fix=f"Create {path} (copy from {name}.example or run `agentos init`).",
            )
            continue
        ok, err = _valid_yaml(path)
        if ok:
            report.add(f"Config: {name}", PASS, "present, valid YAML")
        else:
            report.add(f"Config: {name}", FAIL, f"invalid YAML: {err}", fix=f"Fix the YAML in {path}.")

    # user.yaml: real config preferred, example acceptable-with-warning, neither = FAIL.
    user = cdir / "user.yaml"
    user_example = cdir / "user.yaml.example"
    if user.exists():
        ok, err = _valid_yaml(user)
        if ok:
            report.add("Config: user.yaml", PASS, "present, valid YAML")
        else:
            report.add("Config: user.yaml", FAIL, f"invalid YAML: {err}", fix=f"Fix the YAML in {user}.")
    elif user_example.exists():
        report.add(
            "Config: user.yaml", WARN, "only user.yaml.example present (using defaults)",
            fix="Run `agentos init` (or cp config/user.yaml.example config/user.yaml) and fill it in.",
        )
    else:
        report.add(
            "Config: user.yaml", FAIL, "neither user.yaml nor user.yaml.example found",
            fix="Restore config/user.yaml.example from the repo, then run `agentos init`.",
        )


def _check_credentials(report: DoctorReport) -> None:
    """Can we reach an inference provider at all?

    PASS if EITHER an ANTHROPIC_API_KEY resolves (env or .env) OR the `claude` CLI is
    on PATH. FAIL only if neither — that's the one case where no run can happen.
    """
    env_file = config.ENV_FILE
    env_present = env_file.exists()

    key = config.get_api_key("claude")
    cli = shutil.which("claude")

    if key or cli:
        if key and cli:
            how = ".env credential file present" if env_present else ""
            detail = "ANTHROPIC_API_KEY resolvable + `claude` CLI on PATH"
            detail = f"{detail}{(' (' + how + ')') if how else ''}"
        elif key:
            detail = "ANTHROPIC_API_KEY resolvable"
            detail += " (from config/credentials/.env)" if (env_present and not os.environ.get("ANTHROPIC_API_KEY")) else ""
        else:
            detail = "`claude` CLI on PATH (subscription billing)"
        report.add("Credentials: provider reachable", PASS, detail)
    else:
        report.add(
            "Credentials: provider reachable", FAIL,
            "no ANTHROPIC_API_KEY and no `claude` CLI",
            fix="Add ANTHROPIC_API_KEY to config/credentials/.env, or install the `claude` CLI and `/login`.",
        )


def _check_external_tools(report: DoctorReport) -> None:
    """Optional CLIs — WARN (not FAIL) if missing; they're nice-to-haves."""
    tools = {
        "git": "version control for project repos",
        "gog": "Google calendar/gmail integration (gog CLI)",
        "claude": "provider/runtime CLI (subscription billing)",
    }
    for tool, why in tools.items():
        path = shutil.which(tool)
        if path:
            report.add(f"Tool: {tool}", PASS, path)
        else:
            report.add(f"Tool: {tool}", WARN, f"not on PATH — {why}", fix=f"Install `{tool}` if you need it (optional).")


def _check_workspaces_and_memory(report: DoctorReport) -> None:
    root = config.AGENTOS_ROOT

    # personal workspace — required.
    personal = root / config.personal_dir()
    if personal.is_dir():
        report.add("Workspace: personal", PASS, str(personal))
    else:
        report.add(
            "Workspace: personal", FAIL, f"missing: {personal}",
            fix=f"Create {personal} (mkdir -p) or fix personal_dir in config/user.yaml.",
        )

    # business workspace — only required if any project is configured under it.
    try:
        wants_business = any(
            (cfg or {}).get("workspace") == "business" for cfg in config.projects().values()
        )
    except Exception:  # noqa: BLE001
        wants_business = False
    if wants_business:
        business = root / "workspaces" / "business"
        if business.is_dir():
            report.add("Workspace: business", PASS, str(business))
        else:
            report.add(
                "Workspace: business", WARN, f"projects configured but {business} missing",
                fix=f"Create {business} (mkdir -p) for your business-workspace projects.",
            )

    # global memory dir — the always-loaded tier.
    mem = root / "global" / "memory"
    if mem.is_dir():
        report.add("Memory: global/memory", PASS, str(mem))
    else:
        report.add(
            "Memory: global/memory", WARN, f"missing: {mem}",
            fix="Restore global/memory from the repo (global identity + rules live here).",
        )


def _check_agents_and_workflows(report: DoctorReport) -> None:
    root = config.AGENTOS_ROOT
    # Recompute dirs from the live root rather than the loaders' import-time globals,
    # so a monkeypatched temp root is honored without re-importing those modules.
    agents_dir = root / "agents"
    workflows_dir = root / "workflows"

    try:
        from agentos.core.agent_loader import load_agent

        if not agents_dir.is_dir():
            report.add(
                "Agents: load", WARN, f"no agents dir: {agents_dir}",
                fix="Restore agents/*/agent.md from the repo.",
            )
        else:
            specs = [
                d for d in sorted(agents_dir.iterdir())
                if d.is_dir() and not d.name.startswith(".") and (d / "agent.md").exists()
            ]
            loaded: list[str] = []
            broken: list[str] = []
            # load_agent print()s a warning for an unparseable spec; capture it so the
            # checklist stays clean and surface the drift as a WARN instead.
            with contextlib.redirect_stdout(io.StringIO()):
                for d in specs:
                    if load_agent(d):
                        loaded.append(d.name)
                    else:
                        broken.append(d.name)
            if broken:
                report.add(
                    "Agents: load", WARN,
                    f"{len(loaded)} loaded, {len(broken)} unparseable ({', '.join(broken)})",
                    fix="Fix the frontmatter in the listed agents/*/agent.md (often unquoted YAML).",
                )
            elif loaded:
                report.add("Agents: load", PASS, f"{len(loaded)} agent(s) loaded")
            else:
                report.add(
                    "Agents: load", WARN, "agents dir present but 0 loaded",
                    fix="Check agents/*/agent.md exist and have valid frontmatter.",
                )
    except Exception as e:  # noqa: BLE001
        report.add("Agents: load", FAIL, f"loader raised: {e}", fix="Fix the agent spec(s) the error points at.")

    try:
        from agentos.core.workflow_loader import WorkflowError, parse_workflow

        if not workflows_dir.is_dir():
            report.add(
                "Workflows: load", WARN, f"no workflows dir: {workflows_dir}",
                fix="Restore workflows/*.yaml from the repo.",
            )
        else:
            count = 0
            bad: list[str] = []
            for path in sorted(workflows_dir.glob("*.yaml")):
                try:
                    parse_workflow(yaml.safe_load(path.read_text(encoding="utf-8")), source_path=str(path))
                    count += 1
                except (WorkflowError, yaml.YAMLError) as e:
                    bad.append(f"{path.name}: {str(e).splitlines()[0]}")
            if bad:
                report.add(
                    "Workflows: load", WARN, f"{count} valid, {len(bad)} invalid ({bad[0]})",
                    fix="Fix the invalid workflow YAML.",
                )
            elif count:
                report.add("Workflows: load", PASS, f"{count} workflow(s) loaded")
            else:
                report.add(
                    "Workflows: load", WARN, "workflows dir present but 0 loaded",
                    fix="Add workflows/*.yaml or restore them from the repo.",
                )
    except Exception as e:  # noqa: BLE001
        report.add("Workflows: load", FAIL, f"loader raised: {e}", fix="Fix the workflow spec(s) the error points at.")


def run_checks() -> DoctorReport:
    """Run every health check and return the structured report.

    Never raises — every check captures its own failure as a FAIL/WARN row so the
    command always renders a full checklist and a usable summary.
    """
    report = DoctorReport()
    _check_root(report)
    _check_package(report)
    _check_config_files(report)
    _check_credentials(report)
    _check_external_tools(report)
    _check_workspaces_and_memory(report)
    _check_agents_and_workflows(report)
    return report
