#!/bin/sh
# Graceful drain for the WSGI pools (docs/09 2.5 and 4.8).
#
# On SIGTERM this wrapper (1) touches HUB_DRAIN_FILE, so /api/v1/health/ready answers 503
# and the balancer removes the replica after two failed checks (4 s); (2) waits the drain
# margin while the replica still serves its in-flight requests; (3) forwards SIGTERM so
# gunicorn finishes within graceful_timeout and exits. The orchestrator's stop grace period
# is the drain margin plus graceful_timeout (40 s in docs/09).
#
# Exit status: the child's, so a crashed server is never reported as a clean drain while a
# SIGTERM stop still exits 0.
set -eu

DRAIN_FILE="${HUB_DRAIN_FILE:-/tmp/draining}"
DRAIN_SECONDS="${HUB_DRAIN_SECONDS:-6}"

if [ "$#" -eq 0 ]; then
  echo "usage: entrypoint-drain.sh <command> [args...]" >&2
  exit 64
fi

"$@" &
child=$!

drain() {
  # Readiness only needs the file to exist; its content is irrelevant.
  : >"$DRAIN_FILE"
  sleep "$DRAIN_SECONDS"
  kill -TERM "$child" 2>/dev/null || true
}

trap drain TERM INT

# A trapped signal interrupts `wait` with 128+signal even though nothing went wrong, so
# keep waiting until the child is really gone and its own status is what we report.
status=0
while :; do
  wait "$child" || status=$?
  kill -0 "$child" 2>/dev/null || break
  status=0
done

rm -f "$DRAIN_FILE"
exit "$status"
