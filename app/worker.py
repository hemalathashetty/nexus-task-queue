import asyncio
import signal
import sys
import logging
from sqlalchemy.orm import Session
from datetime import datetime

from app.config import settings
from app.database import SessionLocal
from app.models import Job
from app.redis_client import RedisQueue
from app.tasks import TASK_REGISTRY

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [%(levelname)s] {settings.WORKER_NAME}: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("worker")

# Worker state
keep_running = True

def handle_shutdown(signum, frame):
    global keep_running
    logger.info("Shutdown signal received. Finishing current task...")
    keep_running = False

# Register shutdown signals
signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


# Task-Type Rate Limits: (Max executions, Window in seconds)
RATE_LIMITS = {
    "email": (2, 10),      # Max 2 emails per 10 seconds
    "video": (1, 15),      # Max 1 video per 15 seconds (high visibility for demo!)
    "report": (2, 10),     # Max 2 reports per 10 seconds
    "fail_task": (5, 10)   # Max 5 fail tasks per 10 seconds
}

async def process_job(job_id_str: str, worker_name: str) -> None:
    """Fetches job metadata, checks rate limits, executes the task, and handles errors."""
    db: Session = SessionLocal()
    trace_id = "unknown"
    job_type = None
    payload = None
    priority = 0
    max_retries = 3
    retry_count = 0
    
    try:
        # Phase 1: Read metadata and status validation
        job = db.query(Job).filter(Job.id == job_id_str).first()
        if not job:
            logger.warning(f"[{worker_name}] Job {job_id_str} not found in database. Removing from queue.")
            RedisQueue.remove_from_running(job_id_str)
            return

        trace_id = job.trace_id or "unknown"
        job_type = job.job_type
        payload = job.payload
        priority = job.priority
        max_retries = job.max_retries
        retry_count = job.retry_count

        if job.status == "DEAD" and "cancelled" in (job.error_message or "").lower():
            logger.info(f"[{worker_name}] [Trace: {trace_id}] Job {job_id_str} was already cancelled by client. Skipping.")
            RedisQueue.remove_from_running(job_id_str)
            return

        # Rate Limiting Check
        rl_params = RATE_LIMITS.get(job_type)
        if rl_params:
            limit, window = rl_params
            allowed = RedisQueue.check_rate_limit(job_type, limit, window)
            if not allowed:
                logger.info(f"[{worker_name}] [Trace: {trace_id}] Job {job_id_str} ({job_type}) rate-limited (max {limit}/{window}s). Postponing execution for 3s...")
                job.status = "PENDING"
                db.commit()
                RedisQueue.remove_from_running(job_id_str)
                RedisQueue.add_to_retry(job_id_str, 3)
                return

        # Transition state to RUNNING
        logger.info(f"[{worker_name}] [Trace: {trace_id}] Picked up Job {job_id_str} [{job_type}] with priority {priority}")
        job.status = "RUNNING"
        job.started_at = datetime.utcnow()
        job.worker_name = worker_name
        db.commit()

    except Exception as e:
        logger.error(f"[{worker_name}] [Trace: {trace_id}] Metadata phase failed: {str(e)}")
        RedisQueue.remove_from_running(job_id_str)
        return
    finally:
        db.close()

    # Phase 2: Execution (No open DB transaction/connection)
    try:
        task_func = TASK_REGISTRY.get(job_type)
        if not task_func:
            raise NotImplementedError(f"Job type '{job_type}' is not supported.")

        result = await task_func(payload)
        execution_error = None
    except Exception as e:
        result = None
        execution_error = e

    # Phase 3: Update Outcome
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id_str).first()
        if not job:
            logger.warning(f"[{worker_name}] Job {job_id_str} vanished from database after execution.")
            RedisQueue.remove_from_running(job_id_str)
            return

        if execution_error is None:
            # Success
            job.status = "SUCCESS"
            job.completed_at = datetime.utcnow()
            job.error_message = str(result) if result else None
            db.commit()
            RedisQueue.remove_from_running(job_id_str)
            logger.info(f"[{worker_name}] [Trace: {trace_id}] Finished successfully. Result: {result}")
        else:
            # Failure
            logger.error(f"[{worker_name}] [Trace: {trace_id}] Failed with error: {str(execution_error)}")
            job.retry_count += 1
            
            if job.retry_count <= job.max_retries:
                backoff_delay = 5 * (2 ** (job.retry_count - 1))
                logger.info(f"[{worker_name}] [Trace: {trace_id}] Scheduling retry #{job.retry_count} in {backoff_delay} seconds...")
                job.status = "RETRYING"
                job.error_message = f"Error (Retry #{job.retry_count}): {str(execution_error)}"
                db.commit()
                RedisQueue.remove_from_running(job_id_str)
                RedisQueue.add_to_retry(job_id_str, backoff_delay)
            else:
                logger.warning(f"[{worker_name}] [Trace: {trace_id}] Exceeded max retries ({job.max_retries}). Moving to DEAD (DLQ).")
                job.status = "DEAD"
                job.error_message = f"Exceeded max retries. Last error: {str(execution_error)}"
                job.completed_at = datetime.utcnow()
                db.commit()
                RedisQueue.remove_from_running(job_id_str)
    except Exception as e:
        logger.error(f"[{worker_name}] [Trace: {trace_id}] Outcome commit phase failed: {str(e)}")
        db.rollback()
    finally:
        db.close()


async def worker_loop(worker_name: str = None) -> None:
    """Main worker process loop that polls Redis for tasks."""
    name = worker_name or settings.WORKER_NAME
    logger.info(f"Worker {name} started. Listening for jobs...")
    
    while keep_running:
        try:
            # Atomic Pop & Lock: Move job from pending -> running in Redis
            job_id_str = RedisQueue.pop_job(settings.VISIBILITY_TIMEOUT)
            
            if job_id_str:
                await process_job(job_id_str, name)
            else:
                # No jobs, sleep short duration to avoid high CPU
                await asyncio.sleep(1.0)
                
        except Exception as e:
            logger.error(f"[{name}] Error in worker main loop: {str(e)}")
            await asyncio.sleep(5.0)

    logger.info(f"Worker {name} loop stopped. Exiting gracefully.")

if __name__ == "__main__":
    asyncio.run(worker_loop())
