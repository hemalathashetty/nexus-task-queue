import asyncio
import sys
import logging
from sqlalchemy.orm import Session
from datetime import datetime

from app.config import settings
from app.database import SessionLocal
from app.models import Job
from app.redis_client import RedisQueue

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [%(levelname)s] {settings.SCHEDULER_ID}: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("scheduler")

# Configuration
LEASE_MS = 6000          # 6 seconds leader lease
HEARTBEAT_INTERVAL = 2.0  # check every 2 seconds

async def run_janitor_loop() -> None:
    """Executes janitor tasks: checking visibility timeouts and matured retries."""
    db: Session = SessionLocal()
    try:
        # --- 1. Check Visibility Timeouts ---
        expired_running = RedisQueue.get_expired_running_jobs()
        for job_id_str in expired_running:
            job = db.query(Job).filter(Job.id == job_id_str).first()
            if not job:
                logger.warning(f"Orphaned running job ID {job_id_str} in Redis. Removing.")
                RedisQueue.remove_from_running(job_id_str)
                continue

            # If job is already in a terminal state in the DB, clean up Redis
            if job.status in ["SUCCESS", "DEAD"]:
                logger.info(f"Job {job.id} is in terminal state '{job.status}' in DB. Cleaning Redis.")
                RedisQueue.remove_from_running(job_id_str)
                continue

            # Increment retry count for the crash/hang
            job.retry_count += 1
            logger.warning(f"Visibility timeout expired for Job {job.id} (Worker crashed/hung). Retry count: {job.retry_count}/{job.max_retries}")

            if job.retry_count <= job.max_retries:
                job.status = "PENDING"
                job.error_message = "Visibility timeout expired. Worker did not respond in time."
                db.commit()
                # Move back to pending queue
                RedisQueue.move_running_to_pending(job_id_str, job.priority)
                logger.info(f"Re-queued Job {job.id} into pending list.")
            else:
                job.status = "DEAD"
                job.error_message = "Visibility timeout expired. Exceeded max retries."
                job.completed_at = datetime.utcnow()
                db.commit()
                # Remove from running queue
                RedisQueue.remove_from_running(job_id_str)
                logger.warning(f"Moved Job {job.id} to DEAD queue (DLQ).")

        # --- 2. Check Matured Retry Jobs ---
        expired_retries = RedisQueue.get_expired_retry_jobs()
        for job_id_str in expired_retries:
            job = db.query(Job).filter(Job.id == job_id_str).first()
            if not job:
                logger.warning(f"Orphaned retry job ID {job_id_str} in Redis. Removing.")
                RedisQueue.remove_from_retry(job_id_str)
                continue

            if job.status == "RETRYING":
                logger.info(f"Retry delay matured for Job {job.id}. Re-queueing...")
                job.status = "PENDING"
                db.commit()
                # Move from retry to pending
                RedisQueue.move_retry_to_pending(job_id_str, job.priority)
            else:
                logger.info(f"Retry job {job.id} status is '{job.status}', removing from retry queue.")
                RedisQueue.remove_from_retry(job_id_str)

        # --- 3. Check Matured Delayed Jobs ---
        expired_delayed = RedisQueue.get_expired_delayed_jobs()
        for job_id_str in expired_delayed:
            job = db.query(Job).filter(Job.id == job_id_str).first()
            if not job:
                logger.warning(f"Orphaned delayed job ID {job_id_str} in Redis. Removing.")
                RedisQueue.cancel_job(job_id_str)
                continue

            if job.status == "PENDING":
                logger.info(f"Delay matured for Job {job.id}. Promoting to active pending queue.")
                # Move from delayed to pending
                RedisQueue.move_delayed_to_pending(job_id_str, job.priority)
            else:
                logger.info(f"Delayed job {job.id} status is '{job.status}', removing from delayed queue.")
                RedisQueue.cancel_job(job_id_str)

    except Exception as e:
        logger.error(f"Error during scheduler janitor loop: {str(e)}")
        db.rollback()
    finally:
        db.close()


async def scheduler_loop(scheduler_id: str = None) -> None:
    """Main scheduler process loop handling leader election and periodic maintenance."""
    name = scheduler_id or settings.SCHEDULER_ID
    logger.info(f"Scheduler {name} started. Attempting leader election...")
    is_leader = False

    while True:
        try:
            if not is_leader:
                # Try to acquire leader lease
                acquired = RedisQueue.acquire_leadership(name, LEASE_MS)
                if acquired:
                    is_leader = True
                    logger.info(f"[{name}] ★★★ ELECTED LEADER ★★★ Now executing janitor duties.")
                else:
                    leader_id = RedisQueue.get_leader()
                    logger.info(f"[{name}] Standby mode. Active Leader: {leader_id}")
            else:
                # Renew leadership
                renewed = RedisQueue.renew_leadership(name, LEASE_MS)
                if not renewed:
                    is_leader = False
                    logger.warning(f"[{name}] Leadership lost or failed to renew. Reverting to standby.")
                
            # Only the leader runs janitor jobs
            if is_leader:
                await run_janitor_loop()

            await asyncio.sleep(HEARTBEAT_INTERVAL)

        except Exception as e:
            logger.error(f"[{name}] Error in scheduler main loop: {str(e)}")
            is_leader = False
            await asyncio.sleep(5.0)

if __name__ == "__main__":
    asyncio.run(scheduler_loop())
