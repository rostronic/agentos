#!/usr/bin/env bash
# AgentOS launchd QUIET job runner — for high-frequency jobs (e.g. */5m warmers,
# */30m review queues) where run_job.sh's notify-on-every-run would be spam
# (288 macOS pings/day for warmup alone).
#
# Differences from run_job.sh:
#   - SUCCESS is silent (runner.log line only — cheap audit trail).
#   - FAILURE notifies (macOS + email), but throttled to one notification per
#     6h per job via a marker file, so a persistently-failing */5m job can't
#     flood the channels. The cron log still records every failure.
#
# Usage: run_job_quiet.sh <display_name> <project_dir> <wrapper_rel_path> <log_basename>
set -uo pipefail

NAME="${1:?display name}"; PROJ="${2:?project dir}"; WRAP="${3:?wrapper rel path}"; LOGBASE="${4:?log basename}"
JLOG="${PROJ}/cron/logs/${LOGBASE}.log"
ALOG="${HOME}/agentos/logs/launchd"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NOTIFY="${SCRIPT_DIR}/../notify.sh"
THROTTLE_SECS=21600   # 6h between failure notifications per job
mkdir -p "${ALOG}"

# Don't run (and fail + email) while the Mac is offline; see wait_for_network.sh.
source "${SCRIPT_DIR}/wait_for_network.sh"

cd "${PROJ}" || { echo "cannot cd ${PROJ}" >&2; exit 1; }
bash "${PROJ}/${WRAP}"
rc=$?

printf '%s %s rc=%s\n' "$(date -u +%FT%TZ)" "${NAME}" "${rc}" >> "${ALOG}/runner.log"

if [[ ${rc} -ne 0 ]]; then
  marker="${ALOG}/.quiet_notify_${NAME}"
  now="$(date +%s)"; last="$(cat "${marker}" 2>/dev/null || echo 0)"
  if (( now - last > THROTTLE_SECS )); then
    msg="$(tail -n 3 "${JLOG}" 2>/dev/null | tr '\n' ' ' | sed -E 's/\[[^]]*\] //g' | cut -c1-160)"
    /usr/bin/osascript -e "display notification \"${msg//\"/\'}\" with title \"AgentOS cron\" subtitle \"❌ ${NAME} FAILED (exit ${rc})\"" 2>/dev/null || true
    [[ -x "${NOTIFY}" ]] && bash "${NOTIFY}" email "AgentOS cron — ${NAME} FAILED" "${NAME} failed with exit ${rc}.\n\n${msg}\n\n(throttled: next alert in >=6h)" 2>>"${ALOG}/runner.log" || true
    echo "${now}" > "${marker}"
  fi
fi

exit "${rc}"
