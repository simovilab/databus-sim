# SIMOVI Simulator — single-process Django ASGI image.
#
# ASGI server is `uvicorn`, NOT daphne: the simulator's background tasks
# (tick loop, run-binder, scheduler) are started from the ASGI *lifespan*
# protocol, which daphne 4.x does not emit. uvicorn does. uvicorn[standard]
# also provides the WebSocket implementation Channels needs.
#
# Single-worker invariant: run uvicorn with NO --workers flag (default 1).
# Do NOT front this container with gunicorn/uvicorn multi-worker; the
# InMemoryChannelLayer and in-memory FleetState require exactly ONE process.
# See PLAN §2 and docker-compose.yml for the full rationale.
#
# SECURITY GUARDRAIL: do not expose WEB_PORT to an untrusted network.
# The /databus/ proxy (Phase 7) would otherwise be an unauthenticated gateway
# to the databus write API. AllowAny is deliberate for a local/dev tool only.

FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml .python-version ./

# Install dependencies (no dev deps in production image)
RUN uv sync --no-dev

# Copy the rest of the project
COPY . .

# Collect static files
RUN uv run python manage.py collectstatic --noinput

# Default port — override via WEB_PORT env var in docker-compose.yml
ENV WEB_PORT=8080

# Single uvicorn worker — no --workers flag, ever. See note at top of file.
CMD uv run uvicorn \
    --host 0.0.0.0 \
    --port ${WEB_PORT} \
    sim_project.asgi:application
