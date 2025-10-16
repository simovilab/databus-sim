# Docker Setup for Databus-Sim

This project includes a complete Docker setup with Celery workers, scheduler, and Redis broker.

## Services

- **app**: Main application container
- **celery_worker**: Background task worker
- **celery_beat**: Task scheduler
- **redis**: Message broker and result backend

## Quick Start

1. Build and start all services:
```bash
docker-compose up --build
```

2. Run in background:
```bash
docker-compose up -d --build
```

3. View logs:
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f celery_worker
```

4. Stop services:
```bash
docker-compose down
```

## Development

The project directory is mounted as a volume, so code changes are reflected immediately without rebuilding.

## Environment Variables

- `CELERY_BROKER_URL`: Redis broker URL (default: redis://redis:6379/0)
- `CELERY_RESULT_BACKEND`: Redis result backend URL (default: redis://redis:6379/0)

## Adding Tasks

Add new Celery tasks to `tasks.py` and scheduled tasks to `celery_config.py`.

## Monitoring

- Redis: localhost:6379
- Application: localhost:8000

## Troubleshooting

1. Check if Redis is healthy:
```bash
docker-compose exec redis redis-cli ping
```

2. View worker status:
```bash
docker-compose exec celery_worker celery -A tasks inspect active
```