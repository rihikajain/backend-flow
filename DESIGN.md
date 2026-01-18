# Design Document
## Overview

This document explains the design decisions made for the async job processing service. The service accepts job requests, processes them in the background through a 3-step pipeline (Validate → Transform → Deliver), and delivers results to webhooks.

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              SYSTEM ARCHITECTURE                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

                                    ┌──────────────┐
                                    │    Client    │
                                    │  (curl/app)  │
                                    └──────┬───────┘
                                           │
                              HTTP Request │ POST /jobs
                                           │ GET /jobs/{id}
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              FastAPI Application                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  POST /jobs              GET /jobs/{id}           POST /webhook-receiver │   │
│  │  ─────────              ──────────────           ────────────────────── │   │
│  │  • Validate request     • Query MongoDB          • Receive webhook       │   │
│  │  • Check idempotency    • Return status          • Log delivery          │   │
│  │  • Create job           • Include result         • Acknowledge           │   │
│  │  • Queue to Celery                                                       │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────┬─────────────────────────────────────────────┘
                                    │
                 ┌──────────────────┼──────────────────┐
                 │                  │                  │
                 ▼                  ▼                  ▼
        ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
        │    MongoDB     │ │     Redis      │ │     Redis      │
        │   (job_service)│ │   (Broker)     │ │   (Results)    │
        │                │ │                │ │                │
        │ • jobs         │ │ • Task queue   │ │ • Task results │
        │   collection   │ │ • Messages     │ │ • Expires 1hr  │
        │ • Indexes:     │ │                │ │                │
        │   - doc_id     │ └───────┬────────┘ └────────────────┘
        │   - status     │         │
        │   - created_at │         │ Task dispatch
        └────────┬───────┘         │
                 │                 ▼
                 │    ┌─────────────────────────────────────────┐
                 │    │           Celery Worker                 │
                 │    │  ┌─────────────────────────────────┐   │
                 │    │  │    process_job_pipeline Task    │   │
                 │    │  │                                 │   │
                 │    │  │  ┌─────────┐                    │   │
                 │    │  │  │ Step 1  │ Validate           │   │
                 │    │  │  │ VALIDATE│ • Check payload    │   │
                 │    │  │  └────┬────┘ • Parse JSON       │   │
                 │    │  │       │                         │   │
                 │    │  │       ▼                         │   │
                 │    │  │  ┌─────────┐                    │   │
                 │    │  │  │ Step 2  │ Transform          │   │
  Update status  │    │  │  │TRANSFORM│ • Process data     │   │
  ◄──────────────┼────┼──┼──┤         │ • Compute hash     │   │
                 │    │  │  └────┬────┘ • Add metadata     │   │
                 │    │  │       │                         │   │
                 │    │  │       ▼                         │   │
                 │    │  │  ┌─────────┐                    │   │
                 │    │  │  │ Step 3  │ Deliver            │   │
                 │    │  │  │ DELIVER │ • POST to webhook  │   │
                 │    │  │  │         │ • Retry w/ backoff │   │
                 │    │  │  └────┬────┘ • Idempotency key  │   │
                 │    │  │       │                         │   │
                 │    │  └───────┼─────────────────────────┘   │
                 │    └──────────┼─────────────────────────────┘
                 │               │
                 │               │ HTTP POST (with retries)
                 │               ▼
                 │    ┌─────────────────────────┐
                 │    │   External Webhook      │
                 │    │   (webhook_url)         │
                 │    │                         │
                 │    │ Headers:                │
                 │    │ • X-Idempotency-Key     │
                 │    │ • X-Job-ID              │
                 │    │ • X-Delivery-Attempt    │
                 │    └─────────────────────────┘
                 │
                 └─── Read/Write job state

┌─────────────────────────────────────────────────────────────────────────────────┐
│                              DATA FLOW SUMMARY                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│  1. Client sends POST /jobs with doc_id, payload, webhook_url                   │
│  2. API checks MongoDB for existing job (idempotency)                           │
│  3. If new, creates job in MongoDB (status: PENDING)                            │
│  4. Dispatches task to Redis queue                                              │
│  5. Celery worker picks up task                                                 │
│  6. Worker executes 3-step pipeline, updating MongoDB status                    │
│  7. On success, POSTs result to webhook_url with idempotency headers            │
│  8. Final status (SUCCEEDED/FAILED) saved to MongoDB                            │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                              JOB STATE MACHINE                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│    ┌─────────┐      ┌─────────┐      ┌───────────┐      ┌───────────┐           │
│    │ PENDING │ ───► │ RUNNING │ ───► │ SUCCEEDED │      │           │           │
│    └─────────┘      └────┬────┘      └───────────┘      │           │           │
│                          │                               │  FAILED   │           │
│                          │ (webhook retry)               │           │           │
│                          ▼                               │           │           │
│                    ┌──────────┐                          │           │           │
│                    │ RETRYING │ ────────────────────────►│           │           │
│                    └──────────┘  (max retries exceeded)  └───────────┘           │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```



### A. Key Async Design Decisions and Why

**1. Why Use Asynchronous Processing?**
- **Problem**: If we process jobs immediately in the API, users wait for 3-5 seconds per request
- **Solution**: Return a `job_id` immediately, process in background
- **Benefit**: API responds in milliseconds, users can check status later

**2. Redis as Message Broker**
- **Decision**: Use Redis to queue tasks between API and Worker
- **Why**:
  - Fast (in-memory, very quick message passing)
  - Simple to set up (one Docker container)
  - Works well with Celery
- **Alternative**: RabbitMQ (more complex, better guarantees, but overkill here)

**3. MongoDB for Job Storage**
- **Decision**: Store all job data in MongoDB
- **Why**:
  - Jobs are JSON documents - MongoDB is perfect for this
  - No fixed schema - can add fields easily
  - Good for querying by status, date, doc_id
  - Persists even if services restart
- **Alternative**: Store in Redis
- **Why not**: Redis is for temporary data, MongoDB is for permanent records

### B. How Idempotency is Handled

**What is Idempotency?** Making the same request multiple times produces the same result (no duplicates).

**1. Job Creation Idempotency**
- **How**: Use `doc_id` as unique identifier
- **Mechanism**:
  1. Check if job with same `doc_id` already exists
  2. If yes → return existing job (no duplicate)
  3. If no → create new job
  4. Handle race conditions with database unique index
- **Why needed**: User might submit same document twice, network retries, double-clicks
- **Code approach**: MongoDB unique index on `doc_id` + check-before-create pattern

**2. Task Execution Idempotency**
- **How**: Store job status and track if already processed
- **Mechanism**:
  - Each job has `status` field (PENDING, RUNNING, SUCCEEDED, FAILED)
  - Before processing, check if status is already SUCCEEDED
  - Store `celery_task_id` to track which task processed the job
- **Celery safety**: `task_acks_late=True` means task only marked done after completion
- **Why needed**: If worker crashes and task is re-queued, we don't process twice

**3. Webhook Delivery Idempotency**
- **How**: Multiple layers of protection
- **Mechanism**:
  1. Check `delivered_at` timestamp in database - if set, skip delivery
  2. Generate unique `delivery_id` for each attempt
  3. Send `X-Idempotency-Key` header to webhook receiver
  4. Webhook receiver can check this header to avoid duplicate processing
- **Why needed**: Network retries might send webhook multiple times

### C. Retry Strategy and Failure Handling

**1. Webhook Delivery Retries**
- **Strategy**: Exponential backoff (wait longer each time)
- **How it works**:
  - First attempt: Immediate
  - Second attempt: Wait 2 seconds (2^1)
  - Third attempt: Wait 4 seconds (2^2)
  - Fourth attempt: Wait 8 seconds (2^3)
  - Fifth attempt: Wait 16 seconds (2^4)
  - After 5 retries: Mark job as FAILED
- **Why exponential**: Gives temporary issues time to resolve without overwhelming the webhook
- **What triggers retry**: Network timeouts, HTTP 5xx errors, connection failures
- **What doesn't retry**: HTTP 4xx errors (client errors won't improve)

**2. Job Status Tracking**
- **States**: PENDING → RUNNING → SUCCEEDED (or FAILED via RETRYING)
- **Error handling**:
  - Validation fails → Status = FAILED, error saved
  - Transform fails → Status = FAILED, error saved
  - Delivery fails after retries → Status = FAILED, error saved
- **Each failure**: Error message stored in `error_message` field for debugging

**3. Crash Recovery**
- **If worker crashes**:
  1. Celery re-queues the task (because `task_acks_late=True`)
  2. New worker picks up task
  3. Idempotency checks prevent duplicate processing
  4. Job continues from where it left off

### D. Where Job State Lives and Why

**Three places store different types of data:**

**1. MongoDB - Permanent Job Records**
- **What's stored**: Complete job data (doc_id, payload, status, result, timestamps)
- **Why MongoDB**: 
  - Permanent storage (survives restarts)
  - Can query by any field (status, date, doc_id)
  - Supports complex queries
  - Good for audits and reporting
- **Example**: Find all failed jobs from last week

**2. Redis (Broker) - Task Queue**
- **What's stored**: Tasks waiting to be processed
- **Why Redis**: 
  - Very fast (in-memory)
  - Perfect for queues
  - Temporary (tasks removed after processing)
- **Lifecycle**: Task added → Worker picks up → Task removed

**3. Redis (Results) - Task Results (Temporary)**
- **What's stored**: Celery task execution results
- **Why Redis**: 
  - Temporary cache (expires in 1 hour)
  - Quick access if needed immediately
- **Note**: Main job data is in MongoDB, this is just for Celery's internal tracking

**Summary**: MongoDB = permanent truth, Redis = temporary messaging

### E. What Would Change for Production Readiness (Improvements)

1. **MongoDB Replica Set**
   - Currently: Single MongoDB instance
   - Production: Multiple MongoDB instances (one primary, others backup)
   - Why: If one fails, others take over automatically (high availability)


2. **Authentication & Security**
   - Currently: No authentication
   - Production: API keys, JWT tokens, MongoDB username/password
   - Why: Prevent unauthorized access

3. **Monitoring & Alerting**
   - Currently: Basic logging
   - Production: 
     - Metrics dashboard
     - Alerts when failure rate is high
     - Health checks for all services
   - Why: Know when something is wrong before users complain

4. **Rate Limiting**
   - Currently: No limits
   - Production: Limit requests per client (e.g., 100 jobs/hour per API key)
   - Why: Prevent abuse, ensure fair usage

5. **Better Error Handling**
   - Currently: Jobs fail permanently after retries
   - Production: Dead Letter Queue (store failed jobs for manual review)
   - Why: Some failures might be fixable by adjusting data and retrying

6. **Batching**
   - Process multiple small jobs together
   - Why: More efficient for high volume

7. **Load Testing**
    - Test how system handles 1000s of jobs per second
    - Why: Ensure it scales for production traffic

   **Unit Tests**
   - Validation logic (input parsing)
   - Transform logic (hash consistency)
   - Idempotency checks

   **Integration Tests**
   - API endpoint behavior
   - MongoDB operations (mocked)
   - Celery task mocking

