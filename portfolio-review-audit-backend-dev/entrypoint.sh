#!/bin/sh
set -e
echo "[entrypoint] Starting pr-audit-api..."
echo "[entrypoint] Running database migrations..."
doppler run -- alembic -c ./alembic.ini upgrade head
echo "[entrypoint] Migrations complete."
echo "[entrypoint] Starting Gunicorn..."
exec doppler run -- gunicorn src.main:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --timeout 600 \
    --bind 0.0.0.0:8000
