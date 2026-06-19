from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
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

@router.post("/jobs", response_model=JobResponse, status_code=201)
def create_job(job_in: JobCreate, db: Session = Depends(get_db)):
    """Creates a new background job and enqueues it."""
    # 1. Save job metadata to PostgreSQL
    db_job = Job(
        job_type=job_in.job_type,
        status="PENDING",
        priority=job_in.priority,
        max_retries=job_in.max_retries,
        payload=job_in.payload,
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    # 2. Push job ID to Redis pending ZSET
    try:
        RedisQueue.push_job(str(db_job.id), db_job.priority)
    except Exception as e:
        # Fallback cleanup: If Redis fails, update DB job to FAILED
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
    db_job = db.query(Job).filter(Job.id == job_id).first()
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found")
    return db_job


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: UUID, db: Session = Depends(get_db)):
    """Cancels a pending or retrying job and removes it from the queue."""
    db_job = db.query(Job).filter(Job.id == job_id).first()
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
        db_job = Job(
            job_type=job_data["job_type"],
            status="PENDING",
            priority=job_data["priority"],
            max_retries=3,
            payload=job_data["payload"]
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
        
        RedisQueue.push_job(str(db_job.id), db_job.priority)
        seeded_ids.append(str(db_job.id))

    return {"message": "Successfully seeded test jobs", "seeded_job_ids": seeded_ids}
