#!/usr/bin/env bash
# Sourced by run_job.sh / run_job_quiet.sh before a job runs.
#
# Why: launchd fires missed jobs when the Mac wakes, including brief maintenance
# "DarkWakes" and times the laptop is simply offline (e.g. camping). The jobs then
# fail on DNS lookups and email a failure for every run (2026-10-07/08: ~30 alerts).
#
# Behaviour: if there's no internet (no HTTPS 204 from Google), recheck every
# 15 minutes, up to 4 times (about 1 hour). As soon as a check passes, the job runs
# normally. If all 4 rechecks fail, log "skipped (offline)" and exit 0 with no
# notification; the next scheduled run picks up the work.
#
# Requires: NAME and ALOG set by the caller.

NET_RECHECKS="${NET_RECHECKS:-4}"
NET_RECHECK_SECS="${NET_RECHECK_SECS:-900}"

_net_up() {
  /usr/bin/curl -s -o /dev/null -m 8 -w '%{http_code}' https://www.google.com/generate_204 2>/dev/null | grep -q '^204$'
}

if ! _net_up; then
  _net_start="$(date +%s)"
  printf '%s %s offline, rechecking every %ss up to %s times\n' "$(date -u +%FT%TZ)" "${NAME}" "${NET_RECHECK_SECS}" "${NET_RECHECKS}" >> "${ALOG}/runner.log"
  _net_ok=0
  for (( _i = 1; _i <= NET_RECHECKS; _i++ )); do
    sleep "${NET_RECHECK_SECS}"
    if _net_up; then _net_ok=1; break; fi
  done
  if (( ! _net_ok )); then
    printf '%s %s skipped (offline after %s rechecks)\n' "$(date -u +%FT%TZ)" "${NAME}" "${NET_RECHECKS}" >> "${ALOG}/runner.log"
    exit 0
  fi
  printf '%s %s network back after %ss\n' "$(date -u +%FT%TZ)" "${NAME}" "$(( $(date +%s) - _net_start ))" >> "${ALOG}/runner.log"
fi
