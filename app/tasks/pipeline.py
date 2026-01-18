"""Celery tasks for the job processing pipeline.

Pipeline steps:
1. Validate input metadata
2. Transform data (simulate CPU work)
3. Deliver result to webhook (with retries, backoff, and idempotency)
"""

import json
import time
import uuid
import hashlib
from datetime import datetime
from typing import Any, Optional

import httpx

from celery_app import celery_app
from app.config import get_settings
from app.database import get_sync_db
from app.models import JobStatus
from app.logging_config import get_logger

settings = get_settings()
logger = get_logger("pipeline")


def get_job_sync(job_id: str) -> Optional[dict]:
    """Get job by ID using sync MongoDB client."""
    db = get_sync_db()
    return db.jobs.find_one({"_id": job_id})


def update_job_status(
    job_id: str,
    status: JobStatus,
    current_step: str = None,
    result: dict = None,
    error_message: str = None,
    delivery_id: str = None,
    delivered_at: datetime = None,
    increment_delivery_attempts: bool = False,
) -> None:
    """Update job status in MongoDB."""
    db = get_sync_db()
    
    update_data = {
        "status": status.value,
        "updated_at": datetime.utcnow(),
    }
    
    if current_step is not None:
        update_data["current_step"] = current_step
    if result is not None:
        update_data["result"] = result
    if error_message is not None:
        update_data["error_message"] = error_message
    if delivery_id is not None:
        update_data["delivery_id"] = delivery_id
    if delivered_at is not None:
        update_data["delivered_at"] = delivered_at
    if status == JobStatus.SUCCEEDED:
        update_data["completed_at"] = datetime.utcnow()
    
    update_ops = {"$set": update_data}
    
    if increment_delivery_attempts:
        update_ops["$inc"] = {"delivery_attempts": 1}
    
    db.jobs.update_one({"_id": job_id}, update_ops)
    
    logger.info(
        "job_status_updated",
        job_id=job_id,
        status=status.value,
        current_step=current_step,
    )


def check_delivery_idempotency(job_id: str, delivery_id: str) -> bool:
    """Check if this delivery has already been completed.
    
    Returns True if delivery should proceed, False if already delivered.
    """
    db = get_sync_db()
    job = db.jobs.find_one({"_id": job_id})
    
    if job and job.get("delivered_at"):
        logger.info(
            "duplicate_delivery_prevented",
            job_id=job_id,
            delivery_id=delivery_id,
            original_delivery_at=job["delivered_at"].isoformat(),
        )
        return False
    return True


@celery_app.task(bind=True, name="pipeline.process_job")
def process_job_pipeline(self, job_id: str) -> dict:
    """Main orchestrator task for the job processing pipeline.
    
    Executes the 3-step pipeline:
    1. Validate → 2. Transform → 3. Deliver
    
    Uses a single task with steps rather than chaining for better
    state management and error handling.
    """
    log = logger.bind(job_id=job_id, task_id=self.request.id)
    log.info("pipeline_started")
    
    try:
        # Get job details
        job = get_job_sync(job_id)
        if not job:
            log.error("job_not_found")
            return {"status": "error", "message": "Job not found"}
        
        # Update to RUNNING
        update_job_status(job_id, JobStatus.RUNNING, current_step="validate")
        
        # Step 1: Validate
        log.info("step_started", step="validate")
        payload = job["payload"]
        validation_result = step_validate(job_id, payload)
        if not validation_result["valid"]:
            update_job_status(
                job_id,
                JobStatus.FAILED,
                error_message=validation_result.get("error", "Validation failed"),
            )
            return {"status": "failed", "step": "validate", "error": validation_result.get("error")}
        log.info("step_completed", step="validate")
        
        # Step 2: Transform
        update_job_status(job_id, JobStatus.RUNNING, current_step="transform")
        log.info("step_started", step="transform")
        transform_result = step_transform(job_id, validation_result["data"])
        log.info("step_completed", step="transform")
        
        # Step 3: Deliver (with retries)
        update_job_status(job_id, JobStatus.RUNNING, current_step="deliver")
        log.info("step_started", step="deliver")
        
        # Generate unique delivery ID for idempotency
        delivery_id = str(uuid.uuid4())
        
        delivery_result = step_deliver_with_retry(
            job_id=job_id,
            webhook_url=job["webhook_url"],
            result=transform_result,
            delivery_id=delivery_id,
        )
        
        if delivery_result["delivered"]:
            update_job_status(
                job_id,
                JobStatus.SUCCEEDED,
                result=transform_result,
                delivery_id=delivery_id,
                delivered_at=datetime.utcnow(),
            )
            log.info("pipeline_completed", status="success")
            return {"status": "success", "result": transform_result}
        else:
            update_job_status(
                job_id,
                JobStatus.FAILED,
                error_message=delivery_result.get("error", "Delivery failed"),
            )
            log.error("pipeline_failed", step="deliver", error=delivery_result.get("error"))
            return {"status": "failed", "step": "deliver", "error": delivery_result.get("error")}
            
    except Exception as e:
        log.exception("pipeline_error", error=str(e))
        update_job_status(job_id, JobStatus.FAILED, error_message=str(e))
        raise


def step_validate(job_id: str, payload: dict) -> dict:
    """Step 1: Validate input metadata.
    
    Checks:
    - Payload is a valid dict
    - Data types are correct
    """
    log = logger.bind(job_id=job_id, step="validate")
    
    # Payload is already a dict from MongoDB
    if not isinstance(payload, dict):
        log.warning("validation_failed", reason="not_object")
        return {"valid": False, "error": "Payload must be a JSON object"}
    
    log.info("validation_passed")
    return {"valid": True, "data": payload}


def step_transform(job_id: str, data: dict) -> dict:
    """Step 2: Transform data (simulate CPU work).
    
    Performs some simulated processing:
    - Adds processed timestamp
    - Computes a hash of the data
    - Simulates CPU work with sleep
    """
    log = logger.bind(job_id=job_id, step="transform")
    
    # Simulate CPU work (1-2 seconds)
    processing_time = 1.5
    log.info("transform_processing", duration_seconds=processing_time)
    time.sleep(processing_time)
    
    # Transform the data
    transformed = {
        "original_data": data,
        "processed_at": datetime.utcnow().isoformat(),
        "job_id": job_id,
        "data_hash": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16],
        "processing_metadata": {
            "version": "1.0",
            "processor": "job_pipeline",
        }
    }
    
    log.info("transform_completed", data_hash=transformed["data_hash"])
    return transformed


def step_deliver_with_retry(
    job_id: str,
    webhook_url: str,
    result: dict,
    delivery_id: str,
    max_retries: int = None,
) -> dict:
    """Step 3: Deliver result to webhook with retries and backoff.
    
    Features:
    - Exponential backoff between retries
    - Timeout protection
    - Idempotency key in headers
    - Duplicate delivery prevention
    """
    log = logger.bind(job_id=job_id, step="deliver", delivery_id=delivery_id)
    
    if max_retries is None:
        max_retries = settings.webhook_max_retries
    
    # Check if already delivered (idempotency check)
    if not check_delivery_idempotency(job_id, delivery_id):
        return {"delivered": True, "message": "Already delivered"}
    
    attempt = 0
    last_error = None
    
    while attempt <= max_retries:
        attempt += 1
        
        # Update job status to show retry attempt
        if attempt > 1:
            update_job_status(
                job_id,
                JobStatus.RETRYING,
                increment_delivery_attempts=True,
            )
        
        log.info("delivery_attempt", attempt=attempt, max_retries=max_retries)
        
        try:
            # Make the webhook request with idempotency headers
            with httpx.Client(timeout=settings.webhook_timeout_seconds) as client:
                response = client.post(
                    webhook_url,
                    json={
                        "job_id": job_id,
                        "status": "completed",
                        "result": result,
                        "delivered_at": datetime.utcnow().isoformat(),
                    },
                    headers={
                        "X-Idempotency-Key": delivery_id,
                        "X-Job-ID": job_id,
                        "X-Delivery-Attempt": str(attempt),
                        "Content-Type": "application/json",
                    },
                )
                
                if response.status_code in (200, 201, 202, 204):
                    log.info("delivery_success", status_code=response.status_code)
                    return {"delivered": True, "status_code": response.status_code}
                else:
                    last_error = f"Webhook returned status {response.status_code}"
                    log.warning("delivery_failed", status_code=response.status_code)
                    
        except httpx.TimeoutException:
            last_error = "Webhook request timed out"
            log.warning("delivery_timeout", timeout=settings.webhook_timeout_seconds)
        except httpx.RequestError as e:
            last_error = f"Request error: {str(e)}"
            log.warning("delivery_request_error", error=str(e))
        except Exception as e:
            last_error = f"Unexpected error: {str(e)}"
            log.exception("delivery_unexpected_error", error=str(e))
        
        # Calculate backoff for next retry
        if attempt <= max_retries:
            backoff = settings.webhook_retry_backoff_base ** attempt
            log.info("delivery_retry_scheduled", backoff_seconds=backoff)
            time.sleep(backoff)
    
    log.error("delivery_max_retries_exceeded", attempts=attempt, last_error=last_error)
    return {"delivered": False, "error": last_error, "attempts": attempt}
