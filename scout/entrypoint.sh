#!/bin/sh
set -e

# Optional wait-for Redis / RSSHub if needed (simple loop)
# Example:
# if [ -n "$REDIS_URL" ]; then
#   echo "Waiting for Redis..."
#   until nc -z $(echo $REDIS_HOST | sed 's|redis://||; s|:.*||') 6379; do
#     sleep 0.5
#   done
# fi

# Run DB migrations (idempotent)
if [ -f /app/init_db.py ]; then
  echo "[entrypoint] running DB migrations..."
  python /app/init_db.py
else
  echo "[entrypoint] init_db.py not found; skipping migrations"
fi

# Exec the CMD (uvicorn or whatever was provided in Dockerfile/CMD)
exec "$@"
