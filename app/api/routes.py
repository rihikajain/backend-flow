"""API routes for job management."""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_async_db
from app.services import JobService
from app.tasks import process_job_pipeline
from app.logging_config import get_logger

logger = get_logger("api")
router = APIRouter()


# Request/Response Models
class CreateJobRequest(BaseModel):
    """Request model for creating a job."""
    doc_id: str = Field(..., min_length=1, max_length=255, description="Unique document identifier")
    payload: dict = Field(..., description="Job payload data")
    webhook_url: str = Field(..., description="Webhook URL for delivery")


class CreateJobResponse(BaseModel):
    """Response model for job creation."""
    job_id: str
    doc_id: str
    status: str
    is_new: bool
    message: str


class JobStatusResponse(BaseModel):
    """Response model for job status."""
    job_id: str
    doc_id: str
    status: str
    result: Optional[Any] = None
    error_message: Optional[str] = None
    current_step: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    delivery_attempts: int = 0


class WebhookPayload(BaseModel):
    """Webhook payload received from job completion."""
    job_id: str
    status: str
    result: Optional[dict] = None
    delivered_at: Optional[str] = None


class WebhookResponse(BaseModel):
    """Response for webhook receiver."""
    received: bool
    job_id: str
    timestamp: str


# Dependency
def get_job_service() -> JobService:
    """Get job service instance."""
    db = get_async_db()
    return JobService(db)


# Routes
@router.post(
    "/jobs",
    response_model=CreateJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new job",
    description="Create a new async processing job. Idempotent by doc_id.",
)
async def create_job(
    request: CreateJobRequest,
    job_service: JobService = Depends(get_job_service),
) -> CreateJobResponse:
    """Create a new job for async processing.
    
    This endpoint is idempotent by doc_id - submitting the same doc_id
    multiple times will return the existing job instead of creating a duplicate.
    """
    log = logger.bind(doc_id=request.doc_id)
    log.info("create_job_request", webhook_url=request.webhook_url)
    
    # Create job (idempotent)
    job, is_new = await job_service.create_job(
        doc_id=request.doc_id,
        payload=request.payload,
        webhook_url=request.webhook_url,
    )
    
    if is_new:
        # Queue the job for processing
        task = process_job_pipeline.delay(job.id)
        await job_service.update_job_task_id(job.id, task.id)
        
        log.info("job_queued", job_id=job.id, task_id=task.id)
        message = "Job created and queued for processing"
    else:
        log.info("job_already_exists", job_id=job.id, status=job.status)
        message = f"Job already exists with status: {job.status}"
    
    return CreateJobResponse(
        job_id=job.id,
        doc_id=job.doc_id,
        status=job.status,
        is_new=is_new,
        message=message,
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Get job status",
    description="Get the current status and result of a job.",
)
async def get_job_status(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
) -> JobStatusResponse:
    """Get the status and result of a job by ID."""
    log = logger.bind(job_id=job_id)
    log.info("get_job_status_request")
    
    job_status = await job_service.get_job_status(job_id)
    
    if not job_status:
        log.warning("job_not_found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    
    log.info("job_status_retrieved", status=job_status["status"])
    return JobStatusResponse(**job_status)


@router.post(
    "/webhook-receiver",
    response_model=WebhookResponse,
    summary="Webhook receiver",
    description="Endpoint to receive job completion webhooks (for testing).",
)
async def webhook_receiver(
    payload: WebhookPayload,
    request: Request,
) -> WebhookResponse:
    """Receive webhook notifications when jobs complete.
    
    This endpoint simulates an external system receiving job completion
    notifications. In production, this would be a separate service.
    """
    # Extract idempotency headers
    idempotency_key = request.headers.get("X-Idempotency-Key", "unknown")
    delivery_attempt = request.headers.get("X-Delivery-Attempt", "1")
    
    log = logger.bind(
        job_id=payload.job_id,
        idempotency_key=idempotency_key,
        delivery_attempt=delivery_attempt,
    )
    
    log.info(
        "webhook_received",
        status=payload.status,
        has_result=payload.result is not None,
    )
    
    return WebhookResponse(
        received=True,
        job_id=payload.job_id,
        timestamp=datetime.utcnow().isoformat(),
    )


@router.get(
    "/health",
    summary="Health check",
    description="Check if the service is running.",
)
async def health_check() -> dict:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
    }
