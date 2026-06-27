"""Email channel for digests — SMTP, credentials read at runtime.

Sends a plain-text email (e.g. the weekly SEO digest) to the user. Credentials
come from agentos config/credentials/.env at runtime and are NEVER printed, logged, or committed:

    EMAIL_ADDRESS      sender + auth user (e.g. you@gmail.com)
    EMAIL_PASSWORD     SMTP password / Gmail App Password (see note below)
    EMAIL_SMTP_HOST    default smtp.gmail.com
    EMAIL_SMTP_PORT    default 587 (STARTTLS)
    EMAIL_TO           recipient; defaults to AGENTOS_EMAIL_TO env or config user email

If EMAIL_ADDRESS / EMAIL_PASSWORD are absent the send is a **no-op** that returns
EmailResult(sent=False, reason="unconfigured") and logs a one-line, secret-free
explanation of exactly what to set — it never raises and never fails the caller.

  >>> Gmail setup the user must do (the genuine gap):
  1. Enable 2-Step Verification on the Google account.
  2. Create an App Password at https://myaccount.google.com/apppasswords.
  3. Add to config/credentials/.env (agentos's own secret store, gitignored):
         EMAIL_ADDRESS=you@example.com
         EMAIL_PASSWORD=<the 16-char app password>
     (EMAIL_SMTP_HOST/PORT default to Gmail's STARTTLS endpoint.)
"""

from __future__ import annotations

import os
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from agentos.core.config import AGENTOS_ROOT

CREDS_ENV = Path(os.environ.get("AGENTOS_CREDS_ENV", str(AGENTOS_ROOT / "config" / "credentials" / ".env")))


def _default_to() -> str:
    """Default recipient: AGENTOS_EMAIL_TO env, else the configured user email,
    else a generic placeholder. Read lazily so config drives it (no PII baked in)."""
    env = os.environ.get("AGENTOS_EMAIL_TO")
    if env:
        return env
    from agentos.core import config
    return config.user_email() or "you@example.com"


@dataclass
class EmailResult:
    sent: bool
    reason: str  # "ok" | "unconfigured" | "error: <msg>"


def _read_env(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE pairs from an env file. Comments/blank lines ignored.

    Read-only and side-effect-free: does NOT mutate os.environ (so a digest send
    can't leak creds into the process env for later code to log).
    """
    out: dict[str, str] = {}
    if not path.exists():
        return out
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _config(env: dict[str, str] | None = None) -> dict[str, str]:
    """Effective email config: process env overrides config/credentials/.env."""
    file_env = _read_env(CREDS_ENV) if env is None else env

    def pick(key: str, default: str = "") -> str:
        return os.environ.get(key) or file_env.get(key, default)

    return {
        "address": pick("EMAIL_ADDRESS"),
        "password": pick("EMAIL_PASSWORD"),
        "host": pick("EMAIL_SMTP_HOST", "smtp.gmail.com"),
        "port": pick("EMAIL_SMTP_PORT", "587"),
        "to": pick("EMAIL_TO", _default_to()),
    }


def send_email(subject: str, body: str, *, env: dict[str, str] | None = None) -> EmailResult:
    """Send a plain-text email. No-op (sent=False) when unconfigured; never raises."""
    cfg = _config(env)
    if not cfg["address"] or not cfg["password"]:
        sys.stderr.write(
            "email_sender: no SMTP credentials — set EMAIL_ADDRESS and EMAIL_PASSWORD "
            f"in {CREDS_ENV} (Gmail App Password). Skipping email.\n"
        )
        return EmailResult(sent=False, reason="unconfigured")

    msg = EmailMessage()
    msg["From"] = cfg["address"]
    msg["To"] = cfg["to"]
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        port = int(cfg["port"])
        context = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(cfg["host"], port, context=context, timeout=20) as s:
                s.login(cfg["address"], cfg["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], port, timeout=20) as s:
                s.starttls(context=context)
                s.login(cfg["address"], cfg["password"])
                s.send_message(msg)
        return EmailResult(sent=True, reason="ok")
    except Exception as exc:  # noqa: BLE001 — delivery must never crash the caller
        # Never include the password; exc text is SMTP-level only.
        sys.stderr.write(f"email_sender: send failed ({type(exc).__name__}): {exc}\n")
        return EmailResult(sent=False, reason=f"error: {type(exc).__name__}")


def _main() -> int:
    """CLI: `python -m agentos.notify.email_sender <subject>` with body on stdin.

    Returns 0 even when unconfigured/failed, so a scheduled job is never failed
    by a missing email credential (delivery is best-effort).
    """
    subject = sys.argv[1] if len(sys.argv) > 1 else "AgentOS notification"
    body = sys.stdin.read()
    res = send_email(subject, body)
    sys.stderr.write(f"email_sender: {res.reason}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
