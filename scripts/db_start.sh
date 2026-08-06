#!/usr/bin/env bash
# Запускает локальный dev-кластер Postgres проекта (порт 5433, .pgdata_local) —
# это НЕ системный сервис, автостарта нет, нужно поднимать перед демо/тестами.
set -euo pipefail
cd "$(dirname "$0")/.."

PGCTL="/c/Program Files/PostgreSQL/17/bin/pg_ctl.exe"
DATA_DIR="$(pwd)/.pgdata_local/data"

if "$PGCTL" -D "$DATA_DIR" status >/dev/null 2>&1; then
    echo "Уже запущен."
else
    "$PGCTL" -D "$DATA_DIR" -l "$(pwd)/.pgdata_local/server.log" -o "-p 5433" start
fi

"$PGCTL" -D "$DATA_DIR" status
