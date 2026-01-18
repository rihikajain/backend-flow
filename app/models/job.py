"""Job model and status enum for MongoDB."""

import enum
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field


class JobStatus(str, enum.Enum):
    """Job status enumeration."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"


class Job(BaseModel):
    """Job model representing an async processing job."""
    
    id: str = Field(..., alias="_id")
    doc_id: str
    payload: dict
    webhook_url: str
    
    status: JobStatus = JobStatus.PENDING
    result: Optional[dict] = None
    error_message: Optional[str] = None
    
    # Delivery tracking for idempotency
    delivery_id: Optional[str] = None
    delivered_at: Optional[datetime] = None
    delivery_attempts: int = 0
    
    # Task tracking
    celery_task_id: Optional[str] = None
    current_step: Optional[str] = None
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    
    class Config:
        populate_by_name = True
        use_enum_values = True
    
    def to_mongo(self) -> dict:
        """Convert to MongoDB document format."""
        data = self.model_dump(by_alias=True)
        return data
    
    @classmethod
    def from_mongo(cls, doc: dict) -> "Job":
        """Create Job from MongoDB document."""
        if doc is None:
            return None
        # Convert _id to string if it's ObjectId
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return cls(**doc)
