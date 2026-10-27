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
      # `check_django_config.py` runs `manage.py check --deploy --fail-level WARNING` for every
      # settings module (the Verification list's step 6) with the fallback environment a
      # deployment has. Running the four commands bare would load the checkout's .env, find
      # HUB_ENV=local and fail on DEBUG - a warning about the developer's own shell, not the
      # deployable.
      "python tools/check_django_config.py"
      "cd backend && python manage.py makemigrations --check --dry-run --settings=hub.settings.api"
      "make up"
      "python -m pytest tests/test_stack_up.py -q -m integration"
      "docker compose --env-file .env -f infra/docker/compose.yaml config -q"
      "if command -v actionlint >/dev/null 2>&1; then actionlint; else echo 'actionlint is not installed: workflow lint skipped'; fi"
    )
    ;;
  01)
    commands=(
      "make lint"
      "make contracts"
      "cd backend && python -m pytest tests/unit/identity tests/integration/identity tests/integration/admin tests/architecture -q"
      "cd backend && python -m pytest tests/contract/test_auth_matrix.py -q"
      "cd backend && python ../tools/check_django_config.py"
      # The plan's step 6 selects from a schemathesis harness (tests/contract/
      # test_openapi_conformance.py) that this tree does not have yet; it belongs to the
      # verification-tooling track T4. The guard says so out loud instead of collecting
      # nothing, and the deviation is written up in docs/plan/progress.md.
      "if [ -f backend/tests/contract/test_openapi_conformance.py ]; then cd backend && python -m pytest tests/contract/test_openapi_conformance.py -q -k 'Auth or Approvals or Admin'; else echo 'openapi conformance harness is not in this tree (track T4): see docs/plan/progress.md'; fi"
    )
    ;;
  02)
    commands=(
      "make lint"
      "make contracts"
      "cd backend && python -m pytest tests/unit/customers tests/integration/customers tests/integration/meters tests/architecture -q"
      "cd backend && python -m pytest tests/contract/test_registry_matrix.py -q"
      # Same guard as slice 01: the schemathesis harness is track T4 and not in this tree, so the
      # step says so instead of collecting nothing. The deviation is recorded in progress.md.
      "if [ -f backend/tests/contract/test_openapi_conformance.py ]; then cd backend && python -m pytest tests/contract/test_openapi_conformance.py -q -k 'Customers or Meters'; else echo 'openapi conformance harness is not in this tree (track T4): see docs/plan/progress.md'; fi"
      # Two deviations from the slice's literal step 6, both in progress.md: the fixtures have to
      # be in the database before the import can resolve KIN/BASIC, and the plan names an actor
      # address that is not one of the published accounts.
      "cd backend && python manage.py load_fixtures --settings hub.settings.test && python manage.py import_registry --kind customers --file tests/fixtures/registry/customers_ok.csv --dry-run --actor-email support@example.test --settings hub.settings.test"
      "cd backend && python -m pytest tests -k 'slice01 or identity' -q"
    )
    ;;
  03)
    commands=(
      "make lint"
      "make contracts"
      "cd backend && python -m pytest tests/unit/ledger tests/architecture/test_ledger_models.py tests/architecture/test_ledger_leaf.py -q"
      # Twice, as the slice asks: the concurrency cases are the ones that flake.
      "cd backend && python -m pytest tests/integration/ledger -q"
      "cd backend && python -m pytest tests/integration/ledger -q"
      # Step 6 selects by traceability id. The marker is `tc(<id>)`, whose argument pytest
      # cannot filter on, so the whole tagged set runs instead of the FR_04/NFR_05 subset and
      # `tools/check_traceability.py` (inside `make contracts`) owns the id-to-requirement map.
      "cd backend && python -m pytest tests -q -m tc --strict-markers"
      # Step 5 loads db/invariant_tests.sql into a live database, which needs psql and a DSN;
      # the guard says so out loud rather than skipping silently.
      'if command -v psql >/dev/null 2>&1 && [ -n "${HUB_TEST_DB_URL:-}" ]; then psql "${HUB_TEST_DB_URL}" -v ON_ERROR_STOP=1 -f db/invariant_tests.sql; else echo "psql or HUB_TEST_DB_URL is not set: db/invariant_tests.sql not run here (see docs/plan/progress.md)"; fi'
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
