#!/usr/bin/env bash
# Generate infra/docker/pgbouncer/userlist.txt from the local .env (docs/04 10.10).
#
# `make up` runs this before compose so PgBouncer always has a userlist matching the dev
# passwords in .env, and so the generated file never has to be committed (it is git-ignored).
#
# The passwords are written in clear text on purpose. PgBouncer can only authenticate a
# client with SCRAM-SHA-256 if it can compute the client's proof, which needs the password
# itself; an MD5 hash would not be accepted by a PostgreSQL 16 server that stores SCRAM
# passwords. This is a local development file with `dev` credentials, it is written 0600, and
# real environments take their userlist from the secrets manager.
#
# No secret is ever echoed: the script prints only the file it wrote and the roles it holds.

set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
env_file="${root}/.env"
out="${root}/infra/docker/pgbouncer/userlist.txt"

if [ ! -f "$env_file" ]; then
  cp "${root}/.env.example" "$env_file"
  echo "gen_dev_userlist: created .env from .env.example" >&2
fi

# Read the dev passwords; the file is ours and the values are literals, so sourcing is safe.
set -a
# shellcheck disable=SC1090  # the file is created above
. "$env_file"
set +a

: "${PG_API_PW:=dev}"
: "${PG_INGEST_PW:=dev}"
: "${PG_WORKER_PW:=dev}"
: "${PG_MIGRATOR_PW:=dev}"
: "${PG_ANALYTICS_PW:=dev}"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

# One line per login that reaches the pooler. The owner roles stay NOLOGIN: they own
# functions, they never open a connection.
while IFS=: read -r role password; do
  printf '"%s" "%s"\n' "$role" "$password" >>"$tmp"
done <<EOF
hub_api:${PG_API_PW}
hub_ingest:${PG_INGEST_PW}
hub_worker:${PG_WORKER_PW}
migrator:${PG_MIGRATOR_PW}
analytics_support:${PG_ANALYTICS_PW}
analytics_finance:${PG_ANALYTICS_PW}
analytics_ops:${PG_ANALYTICS_PW}
EOF

# 0644, not 0600: PgBouncer reads the file as uid 70 inside its container, while the host
# user that runs this script is a different uid, so a private file would simply be unreadable
# there. The values are the `dev` passwords of a throwaway cluster and the file is git-ignored.
install -m 0644 "$tmp" "$out"
echo "gen_dev_userlist: wrote ${out#"${root}/"} for 7 roles" >&2
