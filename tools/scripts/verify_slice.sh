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
  04)
    commands=(
      "make lint"
      "make contracts"
      "cd backend && python -m pytest tests/unit/payments tests/integration/payments -q -m 'not concurrency'"
      # Five consecutive runs, as the slice asks: the race is the part that flakes, and a
      # single green run of a concurrency suite proves very little (nightly CI runs twenty).
      "cd backend && for i in 1 2 3 4 5; do python -m pytest tests/integration/payments/test_webhook_concurrency.py -q -m concurrency || exit 1; done"
      # The schemathesis harness is track T4 and not in this tree (same guard as slices 01-02).
      "if [ -f backend/tests/contract/test_openapi_conformance.py ]; then cd backend && python -m pytest tests/contract/test_openapi_conformance.py -q -k 'top_ups or webhooks or webhook_events'; else echo 'openapi conformance harness is not in this tree (track T4): see docs/plan/progress.md'; fi"
      "cd backend && python -m pytest tests/contract/test_auth_matrix.py -q"
      "python -m pytest tests -q"
      "python tools/check_traceability.py"
      # The end-to-end case of Task 04.11: `make up` starts the provider double on the `stubs`
      # profile, and the module provisions its own customer and meter so it can run as often as
      # the suite runs. A skip is a failure here, like everywhere else in this script.
      "make up && cd backend && python -m pytest tests/e2e/test_topup_with_stub.py -q -m e2e"
      "cd backend && python ../tools/check_django_config.py"
    )
    ;;
  T1)
    commands=(
      # The stub's secrets are what the hub verifies against, so a stale `.env` cannot start the
      # stack at all: `make env` tops it up before anything asks for the values.
      "make env"
      "make test-tools-t1"
      "python tools/scripts/sync_stub_schemas.py --check"
      "python -m pytest tests/test_infra_skeleton.py -q"
      # The double has to come up on a real socket with every secret it insists on: that is the
      # difference between a package and a service (compose passes these same variables). The
      # health answer is the service's own view: which channels it collected from.
      'if docker compose version >/dev/null 2>&1; then docker compose --env-file .env -f infra/docker/compose.yaml --profile stubs up -d --build provider-stub --wait && curl -fsS localhost:9100/health && docker compose --env-file .env -f infra/docker/compose.yaml stop provider-stub; else echo "docker is not available: the provider-stub service is not started here (see docs/plan/progress.md)"; fi'
      # 100,000 rows, the hub's upload cap: the statement has to fit in 10 MiB.
      'cd tools/provider_stub && .venv/bin/python -m provider_stub statement --provider mpesa_cd --rows 100000 --day 2026-10-27 --out /tmp/provider_stub_statement.csv && test $(( $(wc -c < /tmp/provider_stub_statement.csv) )) -lt 10485760'
      "python tools/check_traceability.py"
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
