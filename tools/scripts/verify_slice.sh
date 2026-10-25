#!/usr/bin/env bash
# Run one slice's verification commands (docs/plan/slice-NN-*.md, "Verification").
#
#   tools/scripts/verify_slice.sh 00          run them, in order, stopping at the first failure
#   tools/scripts/verify_slice.sh 00 --print  print them, one per line, and exit
#
# `make verify SLICE=NN` calls this. A suite that reports a skipped test fails the slice: a
# skip is a requirement nobody proved (docs/10 section 3), and the integration suite is
# required to run against real Postgres and Redis rather than quietly pass without them.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "${ROOT}"

usage() {
  cat >&2 <<'EOF'
usage: tools/scripts/verify_slice.sh <slice> [--print]

  <slice>    the two-digit slice number, e.g. 00
  --print    print the commands instead of running them
EOF
}

SLICE=${1:-}
MODE=${2:-run}

if [ -z "${SLICE}" ] || [ "${SLICE}" = "-h" ] || [ "${SLICE}" = "--help" ]; then
  usage
  exit 2
fi

case "${SLICE}" in
  00)
    commands=(
      "make lint"
      "cd backend && python -m pytest tests/unit tests/architecture -q"
      "cd backend && python -m pytest tests/integration -q -m integration -p no:cacheprovider --strict-markers -rs"
      "python -m pytest tests -q"
      "python tools/check_traceability.py"
      "python tools/check_django_config.py"
      "cd backend && python manage.py check --deploy --fail-level WARNING --settings=hub.settings.api"
      "cd backend && HUB_INGEST_ROLE=webhooks python manage.py check --deploy --fail-level WARNING --settings=hub.settings.ingest"
      "cd backend && python manage.py check --deploy --fail-level WARNING --settings=hub.settings.worker"
      "cd backend && python manage.py check --deploy --fail-level WARNING --settings=hub.settings.stream"
      "cd backend && python manage.py makemigrations --check --dry-run --settings=hub.settings.api"
      "make up"
      "python -m pytest tests/test_stack_up.py -q -m integration"
      "docker compose --env-file .env -f infra/docker/compose.yaml config -q"
      "if command -v actionlint >/dev/null 2>&1; then actionlint; else echo 'actionlint is not installed: workflow lint skipped'; fi"
    )
    ;;
  *)
    echo "unknown slice '${SLICE}': add its commands to tools/scripts/verify_slice.sh" >&2
    exit 2
    ;;
esac

case "${MODE}" in
  --print)
    printf '%s\n' "${commands[@]}"
    exit 0
    ;;
  run) ;;
  *)
    usage
    exit 2
    ;;
esac

output=$(mktemp)
trap 'rm -f "${output}"' EXIT

failed=0
for command in "${commands[@]}"; do
  printf '\n==> %s\n' "${command}"
  status=0
  # Each command runs in its own subshell with errexit, nounset and pipefail, so a command
  # written with a pipe fails on either side; the script keeps going only to report which
  # one failed.
  ( set -euo pipefail; eval "${command}" ) 2>&1 | tee "${output}" || status=$?
  if [ "${status}" -ne 0 ]; then
    printf '\nFAILED (%s): %s\n' "${status}" "${command}" >&2
    failed=${status}
    break
  fi
  if grep -qE '[0-9]+ skipped' "${output}"; then
    printf '\nFAILED: a test was skipped, so the requirement is unproven: %s\n' "${command}" >&2
    failed=1
    break
  fi
done

if [ "${failed}" -ne 0 ]; then
  printf '\nSlice %s verification FAILED.\n' "${SLICE}" >&2
  exit "${failed}"
fi

printf '\nSlice %s verification PASSED (%s commands).\n' "${SLICE}" "${#commands[@]}"
