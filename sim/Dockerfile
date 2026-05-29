FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Install the sim package into /app/sim so relative imports (from .fleet ...) work.
COPY __init__.py shapes.json /app/sim/
COPY simulator.py extract_shapes.py fleet.py controller.py state_publisher.py \
     databus_client.py run_binder.py http_control.py scheduler.py \
     /app/sim/

ENV PYTHONPATH=/app

CMD ["uv", "run", "--no-sync", "python", "-m", "sim.simulator"]
