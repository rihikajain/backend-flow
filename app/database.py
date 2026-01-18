"""MongoDB database configuration and client management."""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import MongoClient
from pymongo.database import Database

from app.config import get_settings

settings = get_settings()

# Async MongoDB client for FastAPI
async_client: AsyncIOMotorClient = None
async_db: AsyncIOMotorDatabase = None

# Sync MongoDB client for Celery tasks
sync_client: MongoClient = None
sync_db: Database = None


async def init_db() -> None:
    """Initialize MongoDB connection and create indexes."""
    global async_client, async_db
    
    async_client = AsyncIOMotorClient(settings.mongodb_url)
    async_db = async_client[settings.mongodb_database]
    
    # Create indexes for the jobs collection
    await async_db.jobs.create_index("doc_id", unique=True)
    await async_db.jobs.create_index("status")
    await async_db.jobs.create_index("created_at")


async def close_db() -> None:
    """Close MongoDB connection."""
    global async_client
    if async_client:
        async_client.close()


def get_async_db() -> AsyncIOMotorDatabase:
    """Get async MongoDB database for FastAPI dependency injection."""
    return async_db


def get_sync_db() -> Database:
    """Get sync MongoDB database for Celery tasks."""
    global sync_client, sync_db
    
    if sync_client is None:
        sync_client = MongoClient(settings.mongodb_url)
        sync_db = sync_client[settings.mongodb_database]
    
    return sync_db
