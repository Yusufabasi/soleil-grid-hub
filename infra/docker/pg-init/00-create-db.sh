#!/usr/bin/env bash
# Create the `hub` database the first time the pgdata volume is initialised.
#
# The container starts with POSTGRES_DB=postgres so that nothing is created implicitly: the
# database is created here, by name, and everything inside it comes from the baseline
# migration (`manage.py migrate --settings=hub.settings.migrate`), which makes
# db/baseline/0000_baseline.sql the single authority for the schema (ADR-0020).
#
# Idempotent by construction: the volume is only initialised once, and the guard makes a
# re-run on an existing cluster a no-op.

set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER:-postgres}" --dbname postgres <<'SQL'
SELECT 'CREATE DATABASE hub'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'hub')\gexec
SQL

echo "00-create-db: database hub is present"
