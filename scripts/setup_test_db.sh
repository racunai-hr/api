#!/usr/bin/env bash
# Provisions test_finestar_erp_db with PostGIS.
# Server: docker exec postgis (postgres superuser)
# CI: direct psql to PGHOST (GitHub Actions postgis service)
set -euo pipefail

DB_NAME="${TEST_DATABASE_NAME:-test_finestar_erp_db}"
DB_OWNER="${DATABASE_USER:-postgis}"
PG_SUPERUSER="${POSTGRES_SUPERUSER:-postgres}"

use_docker_postgis() {
  command -v docker >/dev/null \
    && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx postgis
}

psql_super() {
  if use_docker_postgis; then
    docker exec postgis psql -U "$PG_SUPERUSER" -v ON_ERROR_STOP=1 "$@"
  else
    psql -U "$PG_SUPERUSER" -d postgres -v ON_ERROR_STOP=1 \
      -h "${PGHOST:-localhost}" \
      -p "${PGPORT:-5432}" \
      "$@"
  fi
}

psql_super_db() {
  if use_docker_postgis; then
    docker exec postgis psql -U "$PG_SUPERUSER" -d "$DB_NAME" -v ON_ERROR_STOP=1 "$@"
  else
    psql -U "$PG_SUPERUSER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
      -h "${PGHOST:-localhost}" \
      -p "${PGPORT:-5432}" \
      "$@"
  fi
}

psql_super <<SQL
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();

DROP DATABASE IF EXISTS ${DB_NAME};
CREATE DATABASE ${DB_NAME} OWNER ${DB_OWNER} ENCODING 'UTF8';
SQL

psql_super_db -c "CREATE EXTENSION IF NOT EXISTS postgis;"

psql_super_db <<SQL
GRANT ALL ON SCHEMA public TO ${DB_OWNER};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ${DB_OWNER};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ${DB_OWNER};
SQL

echo "Test database ${DB_NAME} ready (PostGIS enabled)."
