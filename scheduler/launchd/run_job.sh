#!/usr/bin/env bash
# AgentOS launchd job runner. Runs a project's existing cron wrapper DIRECTLY (not
# inside an AI agent turn), then reports the result as a macOS notification and an
# email (plus, for SEO jobs, the digest) so scheduled jobs are actually visible. Full
# output stays in the project's cron/logs/<log_basename>.log.
#
# Why direct exec: running jobs inside an agent turn produced spurious `error`
# statuses and silent non-execution even though the scripts themselves exit 0.
#
# Channels (delivery failures are best-effort — they never fail the job):
#   - macOS notification: always, no secrets (user is on the Mac).
#   - Email: one-line result via scheduler/notify.sh.
#   - Email: SEO jobs only, the digest summary (no-op until SMTP creds are set).
#
# Usage: run_job.sh <display_name> <project_dir> <wrapper_rel_path> <log_basename>
set -uo pipefail

NAME="${1:?display name}"; PROJ="${2:?project dir}"; WRAP="${3:?wrapper rel path}"; LOGBASE="${4:?log basename}"
JLOG="${PROJ}/cron/logs/${LOGBASE}.log"
ALOG="${HOME}/agentos/logs/launchd"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NOTIFY="${SCRIPT_DIR}/../notify.sh"   # scheduler/notify.sh
mkdir -p "${ALOG}"

# Don't run (and fail + email) while the Mac is offline; see wait_for_network.sh.
source "${SCRIPT_DIR}/wait_for_network.sh"

cd "${PROJ}" || { echo "cannot cd ${PROJ}" >&2; exit 1; }
bash "${PROJ}/${WRAP}"
rc=$?

if [[ ${rc} -eq 0 ]]; then
  subtitle="✅ ${NAME}"
  summary="$(grep -hoE 'ACTIONABLE_ISSUES=[0-9]+ WATCH=[0-9]+' "${JLOG}" 2>/dev/null | tail -1)"
  review="$(grep -hoE 'review written: .*' "${JLOG}" 2>/dev/null | tail -1 | sed 's/review written: //')"
  msg="${summary:-completed}${review:+  → $(basename "${review}")}"
else
  subtitle="❌ ${NAME} FAILED (exit ${rc})"
  msg="$(tail -n 3 "${JLOG}" 2>/dev/null | tr '\n' ' ' | sed -E 's/\[[^]]*\] //g' | cut -c1-160)"
fi

# macOS notification — no secrets (user is on the Mac).
/usr/bin/osascript -e "display notification \"${msg//\"/\'}\" with title \"AgentOS cron\" subtitle \"${subtitle//\"/\'}\"" 2>/dev/null || true
printf '%s %s rc=%s :: %s\n' "$(date -u +%FT%TZ)" "${NAME}" "${rc}" "${msg}" >> "${ALOG}/runner.log"

# Email — one-line result for EVERY job, via scheduler/notify.sh.
if [[ -x "${NOTIFY}" ]]; then
  bash "${NOTIFY}" email "AgentOS cron — ${subtitle}" "${subtitle}\n\n${msg}" 2>>"${ALOG}/runner.log" || true

  # SEO jobs additionally deliver the digest summary by email. The
  # digest is built from the committed findings_*.json (read-only), so it's the
  # same content the dashboard SEO panel shows.
  if [[ ${rc} -eq 0 && "${LOGBASE}" == *seo* ]]; then
    PY="${HOME}/agentos/orchestrator/.venv/bin/python"
    [[ -x "${PY}" ]] || PY="python3"
    digest="$("${PY}" -c 'from agentos.seo import loader; print(loader.notification_text())' 2>>"${ALOG}/runner.log")"
    if [[ -n "${digest}" ]]; then
      bash "${NOTIFY}" email "AgentOS — Weekly SEO digest" "${digest}" 2>>"${ALOG}/runner.log" || true
    fi

    # Auto-remediation (task "T4"): file a `ready` work-store task for each
    # GENUINE actionable finding in this run's findings_<date>.json, de-duped
    # against existing tasks so a recurring issue is filed once. Creates TASKS
    # ONLY — it spawns no agent, opens no PR, and never deploys. The slug is the
    # repo dir basename (e.g. my-site), which matches the registry.
    # We notify ONLY when NEW tasks were created (n>0), so a quiet week is quiet.
    #
    # One live run, written to a temp file: line 1 is `NEW_SEO_TASKS=<n> ...`
    # (the parseable summary), and `---NOTIFY---` fences the human message body
    # that the notifier sends. The remediation step itself never fails the job.
    SLUG="$(basename "${PROJ}")"
    REMED_OUT="$(mktemp -t seo_remediate.XXXXXX)"
    "${PY}" -m agentos.seo.remediate --slug "${SLUG}" --notify \
      >"${REMED_OUT}" 2>>"${ALOG}/runner.log" || true
    new_count="$(sed -n -E 's/^NEW_SEO_TASKS=([0-9]+).*/\1/p' "${REMED_OUT}" | head -1)"
    printf '%s %s seo-remediation :: %s\n' "$(date -u +%FT%TZ)" "${NAME}" \
      "$(head -1 "${REMED_OUT}")" >> "${ALOG}/runner.log"
    if [[ -n "${new_count}" && "${new_count}" -gt 0 ]]; then
      remed_msg="$(sed -n '/^---NOTIFY---$/,$p' "${REMED_OUT}" | sed '1d')"
      [[ -z "${remed_msg}" ]] && remed_msg="SEO auto-remediation: ${new_count} new task(s) filed for ${SLUG} — see the work store. Remediation is human-gated (no auto-PR, no deploy)."
      bash "${NOTIFY}" email "AgentOS — SEO auto-remediation (${new_count} new)" "${remed_msg}" 2>>"${ALOG}/runner.log" || true
    fi
    rm -f "${REMED_OUT}"
  fi
fi

exit ${rc}
