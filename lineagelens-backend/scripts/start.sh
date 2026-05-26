#!/bin/sh
set -e

if echo "$DATABASE_URL" | grep -q "sqlite"; then
  echo "LineageLens lite mode — SQLite, startup schema checks handled by the app"
else
  echo "LineageLens — running Alembic migrations"
  alembic upgrade head
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8787 --workers 1
