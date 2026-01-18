"""Job service for managing job lifecycle with MongoDB."""

import uuid
from datetime import datetime
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.models import Job, JobStatus
from app.logging_config import get_logger

logger = get_logger("job_service")


class JobService:
    """Service for job CRUD operations and state management."""
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.collection = db.jobs
    
    async def create_job(
        self,
        doc_id: str,
        payload: dict,
        webhook_url: str,
    ) -> tuple[Job, bool]:
        """Create a new job or return existing one (idempotent by doc_id).
        
        Returns:
            Tuple of (job, is_new) where is_new indicates if job was newly created
        """
        log = logger.bind(doc_id=doc_id)
        
        # Check for existing job with same doc_id (idempotency)
        existing = await self.get_job_by_doc_id(doc_id)
        if existing:
            log.info("job_exists", job_id=existing.id, status=existing.status)
            return existing, False
        
        # Create new job
        job_id = str(uuid.uuid4())
        now = datetime.utcnow()
        
        job = Job(
            _id=job_id,
            doc_id=doc_id,
            payload=payload,
            webhook_url=webhook_url,
            status=JobStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        
        try:
            await self.collection.insert_one(job.to_mongo())
            log.info("job_created", job_id=job_id)
            return job, True
            
        except DuplicateKeyError:
            # Race condition: another request created the job
            existing = await self.get_job_by_doc_id(doc_id)
            if existing:
                log.info("job_exists_race_condition", job_id=existing.id)
                return existing, False
            raise
    
    async def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID."""
        doc = await self.collection.find_one({"_id": job_id})
        return Job.from_mongo(doc) if doc else None
    
    async def get_job_by_doc_id(self, doc_id: str) -> Optional[Job]:
        """Get job by doc_id."""
        doc = await self.collection.find_one({"doc_id": doc_id})
        return Job.from_mongo(doc) if doc else None
    
    async def update_job_task_id(self, job_id: str, celery_task_id: str) -> None:
        """Update job with Celery task ID."""
        await self.collection.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "celery_task_id": celery_task_id,
                    "updated_at": datetime.utcnow(),
                }
            }
        )
    
    async def get_job_status(self, job_id: str) -> Optional[dict]:
        """Get job status and result."""
        job = await self.get_job(job_id)
        if not job:
            return None
        
        return {
            "job_id": job.id,
            "doc_id": job.doc_id,
            "status": job.status,
            "result": job.result,
            "error_message": job.error_message,
            "current_step": job.current_step,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "delivery_attempts": job.delivery_attempts,
        }
