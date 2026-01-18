# Async Job Processing Service

A Python service that accepts jobs via an API and processes them asynchronously using Celery. Each job goes through a 3-step pipeline: **Validate → Transform → Deliver**.

## Features

- **RESTful API** built with FastAPI
- **Async Processing** with Celery and Redis
- **MongoDB** for persistent job storage
- **Idempotent Job Creation** - duplicate `doc_id` submissions return existing jobs
- **Reliable Delivery** - webhook delivery with retries, exponential backoff, and idempotency protection
- **Job State Tracking** - full lifecycle visibility (PENDING, RUNNING, SUCCEEDED, FAILED, RETRYING)
- **Structured Logging** - JSON logs with job/task correlation
- **Docker Compose** - one-command setup for the entire stack

## Quick Start

### Using Docker (Recommended)

```bash
# Clone and start all services
docker-compose up --build

# The following services will be available:
# - API:      http://localhost:8000
# - Docs:     http://localhost:8000/docs
# - MongoDB:  localhost:27017
# - Redis:    localhost:6379
```

## API Endpoints

### Create a Job

```bash
POST /jobs
Content-Type: application/json

{
    "doc_id": "unique-document-id",
    "payload": {"key": "value", "data": 123},
    "webhook_url": "http://localhost:8000/webhook-receiver"
}
```

**Response:**
```json
{
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "doc_id": "unique-document-id",
    "status": "PENDING",
    "is_new": true,
    "message": "Job created and queued for processing"
}
```

### Get Job Status

```bash
GET /jobs/{job_id}
```

**Response:**
```json
{
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "doc_id": "unique-document-id",
    "status": "SUCCEEDED",
    "result": {
        "original_data": {"key": "value"},
        "processed_at": "2024-01-15T10:30:00",
        "data_hash": "abc123..."
    },
    "current_step": "deliver",
    "created_at": "2024-01-15T10:29:55",
    "completed_at": "2024-01-15T10:30:00"
}
```

### Webhook Receiver (Test Endpoint)

```bash
POST /webhook-receiver
```

This endpoint simulates an external system receiving job completion notifications.

### Health Check

```bash
GET /health
```

## Job States

| Status | Description |
|--------|-------------|
| `PENDING` | Job created, waiting to be processed |
| `RUNNING` | Job is being processed |
| `RETRYING` | Webhook delivery is being retried |
| `SUCCEEDED` | Job completed successfully |
| `FAILED` | Job failed (validation, transform, or delivery) |

## Configuration

Environment variables (can be set in `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGODB_URL` | `mongodb://mongodb:27017` | MongoDB connection URL |
| `MONGODB_DATABASE` | `job_service` | MongoDB database name |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection URL |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Celery broker URL |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/1` | Celery result backend |
| `WEBHOOK_TIMEOUT_SECONDS` | `30` | Webhook request timeout |
| `WEBHOOK_MAX_RETRIES` | `5` | Max webhook delivery attempts |
| `WEBHOOK_RETRY_BACKOFF_BASE` | `2` | Exponential backoff base |
| `DEBUG` | `false` | Enable debug mode |

### Logs

Structured JSON logs include:
- `job_id` - Job identifier
- `task_id` - Celery task identifier  
- `step` - Current pipeline step
- Event type and metadata

Example log output:
```json
{
    "event": "pipeline_started",
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "task_id": "celery-task-id",
    "timestamp": "2024-01-15T10:29:55.123456Z",
    "level": "info"
}
```