#!/bin/bash
# Runs once, when Postgres starts with an empty data volume.
# Applies db/migrations/*.sql in name order, then db/seed.sql (unless SEED_DATA=false).
# To re-run on an existing stack: docker compose down -v && docker compose up
set -euo pipefail

run_sql() {
  echo "initdb: applying $1"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$1"
}

for f in /db/migrations/*.sql; do
  run_sql "$f"
done

if [ "${SEED_DATA:-true}" = "true" ]; then
  run_sql /db/seed.sql
fi
