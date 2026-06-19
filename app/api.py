import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from uuid import UUID
import uuid

from app.database import get_db
from app.models import Job
from app.schemas import JobCreate, JobResponse, StatsResponse
from app.redis_client import RedisQueue

router = APIRouter(prefix="/api")
logger = logging.getLogger("api")

@router.post("/jobs", response_model=JobResponse, status_code=201)
def create_job(job_in: JobCreate, db: Session = Depends(get_db)):
    """Creates a new background job, assigns a trace ID, and enqueues it."""
    # 1. Backpressure / Load Shedding Check
    try:
        queue_sizes = RedisQueue.get_queue_sizes()
        if queue_sizes["pending"] >= 50:
            logger.warning(f"[API] [Load Shedding] Pending queue depth is {queue_sizes['pending']}. Rejecting job.")
            raise HTTPException(
                status_code=429, 
                detail="Queue capacity limit reached (50 pending tasks). Please try again later (backpressure)."
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Failed to check queue capacity: {str(e)}")

    # 2. Setup Trace ID & run_at if delayed
    trace_id_str = str(uuid.uuid4())
    run_at = None
    if job_in.delay_seconds is not None and job_in.delay_seconds > 0:
        run_at = datetime.utcnow() + timedelta(seconds=job_in.delay_seconds)

    # 3. Save job metadata to SQLite
    db_job = Job(
        job_type=job_in.job_type,
        status="PENDING",
        priority=job_in.priority,
        max_retries=job_in.max_retries,
        payload=job_in.payload,
        trace_id=trace_id_str,
        run_at=run_at
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    # 4. Push job ID to Redis queue
    try:
        if run_at:
            # Route to delayed queue ZSET
            import time
            run_at_ts = time.time() + job_in.delay_seconds
            RedisQueue.push_delayed(str(db_job.id), run_at_ts)
            logger.info(f"[API] [Trace: {trace_id_str}] Enqueued DELAYED Job {db_job.id} (runs in {job_in.delay_seconds}s at {run_at.isoformat()})")
        else:
            # Route directly to pending ZSET
            RedisQueue.push_job(str(db_job.id), db_job.priority)
            logger.info(f"[API] [Trace: {trace_id_str}] Enqueued IMMEDIATE Job {db_job.id} with priority {db_job.priority}")
    except Exception as e:
        # Fallback cleanup
        db_job.status = "FAILED"
        db_job.error_message = f"Failed to push to Redis queue: {str(e)}"
        db.commit()
        raise HTTPException(status_code=500, detail="Broker is currently unavailable. Job marked as failed.")

    return db_job


@router.get("/jobs", response_model=List[JobResponse])
def list_jobs(
    status: Optional[str] = Query(None, description="Filter by job status"),
    job_type: Optional[str] = Query(None, description="Filter by job type"),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """Retrieves a list of jobs with optional filters, sorted by creation date."""
    query = db.query(Job)
    if status:
        query = query.filter(Job.status == status.upper())
    if job_type:
        query = query.filter(Job.job_type == job_type.lower())
    
    return query.order_by(Job.created_at.desc()).limit(limit).all()


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: UUID, db: Session = Depends(get_db)):
    """Retrieves details of a specific job by UUID."""
    db_job = db.query(Job).filter(Job.id == str(job_id)).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    return db_job


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: UUID, db: Session = Depends(get_db)):
    """Cancels a pending or retrying job and removes it from the queue."""
    db_job = db.query(Job).filter(Job.id == str(job_id)).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")

    if db_job.status in ["SUCCESS", "FAILED", "DEAD"]:
        raise HTTPException(status_code=400, detail=f"Cannot cancel job in terminal state: {db_job.status}")

    # Remove from Redis
    RedisQueue.cancel_job(str(db_job.id))

    # Mark as DEAD (cancelled) in PostgreSQL
    db_job.status = "DEAD"
    db_job.error_message = "Job cancelled by client."
    db_job.completed_at = func.now()
    db.commit()
    db.refresh(db_job)

    return db_job


@router.get("/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)):
    """Aggregates real-time statistics from PostgreSQL."""
    stats_query = db.query(Job.status, func.count(Job.id)).group_by(Job.status).all()
    stats_dict = {status: count for status, count in stats_query}

    # Normalize stats to match UI expectations
    stats = StatsResponse(
        total=db.query(func.count(Job.id)).scalar() or 0,
        pending=stats_dict.get("PENDING", 0),
        running=stats_dict.get("RUNNING", 0),
        success=stats_dict.get("SUCCESS", 0),
        failed=stats_dict.get("FAILED", 0),
        retrying=stats_dict.get("RETRYING", 0),
        dead=stats_dict.get("DEAD", 0),
        leader=RedisQueue.get_leader()
    )
    return stats


@router.post("/jobs/seed-test")
def seed_test_jobs(db: Session = Depends(get_db)):
    """Utility endpoint to seed multiple jobs of varying priorities and success rates for testing."""
    test_jobs = [
        # Normal Priority Email Jobs
        {"job_type": "email", "priority": 0, "payload": {"to": "user1@example.com", "subject": "Welcome!"}},
        {"job_type": "email", "priority": 0, "payload": {"to": "user2@example.com", "subject": "Weekly Newsletter"}},
        
        # High Priority Email Job
        {"job_type": "email", "priority": 10, "payload": {"to": "vip@example.com", "subject": "Urgent Alert!", "body": "Critical system event detected."}},
        
        # Failing Email Job (testing retries)
        {"job_type": "email", "priority": 2, "payload": {"to": "buggy_smtp@example.com", "fail_probability": 1.0}},
        
        # Video Processing Jobs (takes longer, multi-step simulation)
        {"job_type": "video", "priority": 5, "payload": {"video_name": "vacation_vlog.mp4", "resolution": "1080p"}},
        {"job_type": "video", "priority": 1, "payload": {"video_name": "tutorial_draft.mov", "resolution": "720p"}},
        
        # Business Reports (medium complexity)
        {"job_type": "report", "priority": 3, "payload": {"report_name": "monthly_financials", "filters": {"year": 2026, "month": 6}}},
        
        # Instant Failure Job (testing DLQ)
        {"job_type": "fail_task", "priority": 0, "payload": {}}
    ]

    seeded_ids = []
    for job_data in test_jobs:
        trace_id_str = str(uuid.uuid4())
        db_job = Job(
            job_type=job_data["job_type"],
            status="PENDING",
            priority=job_data["priority"],
            max_retries=3,
            payload=job_data["payload"],
            trace_id=trace_id_str
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
        
        RedisQueue.push_job(str(db_job.id), db_job.priority)
        seeded_ids.append(str(db_job.id))

    return {"message": "Successfully seeded test jobs", "seeded_job_ids": seeded_ids}


@router.post("/jobs/redrive-dlq")
def redrive_dlq(db: Session = Depends(get_db)):
    """Fetches all DEAD jobs, resets their retries, and pushes them back to the active queue."""
    dead_jobs = db.query(Job).filter(Job.status == "DEAD").all()
    
    redriven_ids = []
    for job in dead_jobs:
        # Reset task metadata
        job.status = "PENDING"
        job.retry_count = 0
        job.error_message = None
        job.worker_name = None
        job.completed_at = None
        job.started_at = None
        
        # Push back into Redis pending queue
        RedisQueue.push_job(str(job.id), job.priority)
        redriven_ids.append(str(job.id))
        
        logger.info(f"[API] [Trace: {job.trace_id}] Redriving DEAD Job {job.id} back to PENDING.")

    db.commit()
    return {"message": f"Successfully redriven {len(redriven_ids)} dead jobs.", "redriven_job_ids": redriven_ids}
