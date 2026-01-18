"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Application
    app_name: str = "Job Processing Service"
    debug: bool = False
    
    # Redis (used as Celery broker and result backend)
    redis_url: str = "redis://redis:6379/0"
    
    # MongoDB
    mongodb_url: str = "mongodb://mongodb:27017"
    mongodb_database: str = "job_service"
    
    # Celery
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"
    
    # Webhook delivery settings
    webhook_timeout_seconds: int = 30
    webhook_max_retries: int = 5
    webhook_retry_backoff_base: int = 2  # exponential backoff base
    
    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
