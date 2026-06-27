#!/usr/bin/env bash
# AgentOS multi-channel notifier for scheduled jobs.
#
# Posts a message to Telegram (and, if configured, email) so cron/launchd job
# results land somewhere you actually see them. Credentials are read at RUNTIME
# from config/credentials/.env — the token is NEVER printed, logged, or committed.
#
# Usage:
#   notify.sh telegram "<text>"          # post text to the configured chat
#   notify.sh email "<subject>" "<body>" # email the digest (no-op if unconfigured)
#
# Configuration (all optional — a missing value just disables that channel):
#   config/credentials/.env   TELEGRAM_BOT_TOKEN=...   (from @BotFather)
#                             TELEGRAM_CHAT_ID=...     (where the bot messages you)
#                             EMAIL_TO=you@example.com
#   config/user.yaml          telegram_chat_id: "..."  (fallback for the chat id)
#
#   Override paths/values via env when invoking (e.g. from launchd):
#     AGENTOS_HOME            project root (default: ${HOME}/agentos)
#     AGENTOS_CREDS_ENV       path to the .env (default: $AGENTOS_HOME/config/credentials/.env)
#     AGENTOS_TELEGRAM_CHAT_ID  explicit chat id, highest precedence
#     AGENTOS_EMAIL_TO        explicit email recipient
#
# Exit code is always 0 for delivery failures (a missing token must not fail the
# job) — diagnostics go to stderr only, never stdout, and never include secrets.
set -uo pipefail

AGENTOS_HOME="${AGENTOS_HOME:-${HOME}/agentos}"
CREDS_ENV="${AGENTOS_CREDS_ENV:-${AGENTOS_HOME}/config/credentials/.env}"
USER_YAML="${AGENTOS_USER_YAML:-${AGENTOS_HOME}/config/user.yaml}"

# Read a single KEY's value from an env file without sourcing it (sourcing would
# execute arbitrary lines). Prints the value to stdout; empty if absent.
read_env_value() {
  local key="$1" file="$2"
  [[ -f "${file}" ]] || return 0
  # First non-comment KEY=... line wins. Strip surrounding quotes.
  sed -n -E "s/^[[:space:]]*${key}=(.*)$/\1/p" "${file}" 2>/dev/null \
    | grep -v '^[[:space:]]*#' | head -n1 \
    | sed -E 's/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/'
}

# Read a simple top-level scalar from a YAML file (e.g. `telegram_chat_id: "123"`)
# without a YAML parser. Strips surrounding quotes. Empty if absent.
read_yaml_value() {
  local key="$1" file="$2"
  [[ -f "${file}" ]] || return 0
  sed -n -E "s/^[[:space:]]*${key}:[[:space:]]*(.*)$/\1/p" "${file}" 2>/dev/null \
    | head -n1 \
    | sed -E 's/[[:space:]]*#.*$//; s/[[:space:]]+$//' \
    | sed -E 's/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/'
}

# Resolve the Telegram chat id from (in precedence order):
#   1. AGENTOS_TELEGRAM_CHAT_ID env override
#   2. TELEGRAM_CHAT_ID in config/credentials/.env
#   3. telegram_chat_id in config/user.yaml
resolve_chat_id() {
  local id="${AGENTOS_TELEGRAM_CHAT_ID:-}"
  [[ -n "${id}" ]] && { printf '%s' "${id}"; return 0; }
  id="$(read_env_value TELEGRAM_CHAT_ID "${CREDS_ENV}")"
  [[ -n "${id}" ]] && { printf '%s' "${id}"; return 0; }
  read_yaml_value telegram_chat_id "${USER_YAML}"
}

notify_telegram() {
  local text="$1"
  local token chat_id
  token="$(read_env_value TELEGRAM_BOT_TOKEN "${CREDS_ENV}")"
  if [[ -z "${token}" ]]; then
    echo "notify.sh: TELEGRAM_BOT_TOKEN not found in ${CREDS_ENV} — skipping Telegram" >&2
    return 0
  fi
  chat_id="$(resolve_chat_id)"
  if [[ -z "${chat_id}" ]]; then
    echo "notify.sh: no Telegram chat id (set TELEGRAM_CHAT_ID in .env or telegram_chat_id in user.yaml) — skipping Telegram" >&2
    return 0
  fi
  # --data-urlencode keeps the token out of argv (it's in the URL path only,
  # which curl does not echo). Suppress the body; never print the token.
  curl -s -o /dev/null --max-time 15 \
    "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat_id}" \
    --data-urlencode "text=${text}" \
    --data-urlencode "disable_web_page_preview=true" \
    || echo "notify.sh: Telegram delivery failed (network)" >&2
  return 0
}

notify_email() {
  local subject="$1" body="$2"
  local to; to="$(read_env_value EMAIL_TO "${CREDS_ENV}")"; to="${to:-${AGENTOS_EMAIL_TO:-}}"
  if [[ -z "${to}" ]]; then
    echo "notify.sh: no EMAIL_TO configured (set it in .env or AGENTOS_EMAIL_TO) — skipping email" >&2
    return 0
  fi
  # Primary: gog (Google CLI) — uses its EXISTING OAuth, NO password / app-password.
  # Body on stdin (--body-file -) so it never hits argv/logs.
  if command -v gog >/dev/null 2>&1; then
    if printf '%s' "${body}" | gog gmail send --account "${to}" --to "${to}" \
        --subject "${subject}" --body-file - >/dev/null 2>>/dev/null; then
      return 0
    fi
    echo "notify.sh: gog gmail send failed — trying SMTP fallback" >&2
  fi
  # Fallback: SMTP via the Python sender (no-op unless EMAIL_PASSWORD is set).
  local py="${AGENTOS_HOME}/orchestrator/.venv/bin/python"
  [[ -x "${py}" ]] || py="python3"
  printf '%s' "${body}" | "${py}" -m agentos.notify.email_sender "${subject}" 2>>/dev/null \
    || echo "notify.sh: email delivery unavailable" >&2
  return 0
}

cmd="${1:-}"
case "${cmd}" in
  telegram) notify_telegram "${2:-}" ;;
  email)    notify_email "${2:-}" "${3:-}" ;;
  *) echo "usage: notify.sh {telegram <text>|email <subject> <body>}" >&2; exit 2 ;;
esac
