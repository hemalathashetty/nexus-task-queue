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


async def process_job(job_id_str: str, worker_name: str) -> None:
    """Fetches job metadata, executes the task, and handles completion or retry routing."""
    db: Session = SessionLocal()
    try:
        # 1. Fetch job details
        job = db.query(Job).filter(Job.id == job_id_str).first()
        if not job:
            logger.warning(f"[{worker_name}] Job {job_id_str} not found in database. Removing from queue.")
            RedisQueue.remove_from_running(job_id_str)
            return

        if job.status == "DEAD" and "cancelled" in (job.error_message or "").lower():
            logger.info(f"[{worker_name}] Job {job_id_str} was already cancelled by client. Skipping.")
            RedisQueue.remove_from_running(job_id_str)
            return

        # 2. Transition state in DB to RUNNING
        logger.info(f"[{worker_name}] picked up Job {job.id} [{job.job_type}] with priority {job.priority}")
        job.status = "RUNNING"
        job.started_at = datetime.utcnow()
        job.worker_name = worker_name
        db.commit()

        # 3. Resolve and execute task
        task_func = TASK_REGISTRY.get(job.job_type)
        if not task_func:
            raise NotImplementedError(f"Job type '{job.job_type}' is not supported.")

        # Run task function
        result = await task_func(job.payload)

        # 4. Success handling
        job.status = "SUCCESS"
        job.completed_at = datetime.utcnow()
        db.commit()
        
        # Remove from running queue in Redis
        RedisQueue.remove_from_running(job_id_str)
        logger.info(f"[{worker_name}] Job {job.id} finished successfully. Result: {result}")

    except Exception as e:
        logger.error(f"[{worker_name}] Job {job_id_str} failed with error: {str(e)}")
        
        # 5. Failure and Retry Logic
        job.retry_count += 1
        
        if job.retry_count <= job.max_retries:
            # Exponential Backoff Delay calculation (e.g. 5, 10, 20 seconds)
            backoff_delay = 5 * (2 ** (job.retry_count - 1))
            logger.info(f"[{worker_name}] Scheduling retry #{job.retry_count} for Job {job.id} in {backoff_delay} seconds...")

            # Transition state in DB to RETRYING
            job.status = "RETRYING"
            job.error_message = f"Error (Retry #{job.retry_count}): {str(e)}"
            db.commit()

            # Remove from running, and push to retry queue in Redis
            RedisQueue.remove_from_running(job_id_str)
            RedisQueue.add_to_retry(job_id_str, backoff_delay)
        else:
            logger.warning(f"[{worker_name}] Job {job.id} has exceeded max retries ({job.max_retries}). Moving to DLQ (DEAD status).")
            
            # Transition state in DB to DEAD
            job.status = "DEAD"
            job.error_message = f"Exceeded max retries. Last error: {str(e)}"
            job.completed_at = datetime.utcnow()
            db.commit()

            # Remove from running in Redis (DEAD states are stored persistently in DB)
            RedisQueue.remove_from_running(job_id_str)

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
