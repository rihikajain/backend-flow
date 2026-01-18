"""Celery application configuration."""

from celery import Celery
from app.config import get_settings

settings = get_settings()

# Create Celery app
celery_app = Celery(
    "job_processor",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.pipeline"],
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    
    # Task execution settings
    task_acks_late=True,  # Acknowledge tasks after completion for reliability
    task_reject_on_worker_lost=True,  # Re-queue tasks if worker crashes
    
    # Result settings
    result_expires=3600,  # Results expire after 1 hour
    
    # Worker settings
    worker_prefetch_multiplier=1,  # One task at a time for better reliability
    
    # Retry settings
    task_default_retry_delay=5,
    task_max_retries=3,
    
    # Beat schedule (if needed for future scheduled tasks)
    beat_schedule={},
)
