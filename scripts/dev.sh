#!/usr/bin/env bash
# Local (non-Docker) launcher for the SIMOVI simulator.
#
# Reads .env (if present) so WEB_PORT and the DATABUS_* knobs are honored, then
# starts the single uvicorn ASGI process. Django settings also load .env via
# python-dotenv, so app config and this script's port stay in sync.
#
# Usage:
#   ./scripts/dev.sh            # uses WEB_PORT from .env (or 8080)
#   WEB_PORT=9000 ./scripts/dev.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# Load .env into the environment if it exists (so $WEB_PORT below is set).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

WEB_PORT="${WEB_PORT:-8080}"

echo "Starting SIMOVI simulator on http://0.0.0.0:${WEB_PORT} (single uvicorn worker)…"
exec uv run uvicorn \
  --host 0.0.0.0 \
  --port "${WEB_PORT}" \
  sim_project.asgi:application
