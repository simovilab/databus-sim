FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy uv project files
COPY pyproject.toml uv.lock* ./

# Install dependencies using uv
RUN uv sync --frozen

# Copy application code
COPY . .

# Create assets directory if it doesn't exist
RUN mkdir -p assets

# Expose port for potential API
EXPOSE 8000

# Default command (can be overridden in docker-compose)
CMD ["uv", "run", "python", "main.py"]