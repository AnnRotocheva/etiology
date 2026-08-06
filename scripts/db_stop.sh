#!/usr/bin/env bash
# Останавливает локальный dev-кластер Postgres проекта (см. db_start.sh).
set -euo pipefail
cd "$(dirname "$0")/.."

"/c/Program Files/PostgreSQL/17/bin/pg_ctl.exe" -D "$(pwd)/.pgdata_local/data" stop
