import redis
import time
from typing import Optional, List
from app.config import settings

# Initialize Redis Connection (with fakeredis fallback)
if settings.REDIS_URL.lower() == "mock":
    import fakeredis
    redis_conn = fakeredis.FakeRedis(decode_responses=True)
else:
    try:
        redis_conn = redis.from_url(settings.REDIS_URL, decode_responses=True)
        redis_conn.ping()
    except Exception as e:
        import logging
        logging.getLogger("redis").warning(f"Could not connect to Redis at {settings.REDIS_URL} ({str(e)}). Falling back to fakeredis.")
        import fakeredis
        redis_conn = fakeredis.FakeRedis(decode_responses=True)

# Lua Scripts
POP_JOB_LUA = """
local job = redis.call('ZPOPMAX', KEYS[1])
if job[1] then
    local job_id = job[1]
    local score = ARGV[1]
    redis.call('ZADD', KEYS[2], score, job_id)
    return job_id
else
    return nil
end
"""

RENEW_LEADERSHIP_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('PEXPIRE', KEYS[1], ARGV[2])
    return 1
else
    return 0
end
"""

# Register scripts
pop_job_script = redis_conn.register_script(POP_JOB_LUA)
renew_leadership_script = redis_conn.register_script(RENEW_LEADERSHIP_LUA)

class RedisQueue:
    PENDING_KEY = "queue:pending"
    RUNNING_KEY = "queue:running"
    RETRY_KEY = "queue:retry"
    LEADER_KEY = "scheduler:leader"

    @staticmethod
    def push_job(job_id: str, priority: int) -> None:
        """Pushes a job to the pending queue with a priority score."""
        redis_conn.zadd(RedisQueue.PENDING_KEY, {job_id: priority})

    @staticmethod
    def pop_job(visibility_timeout: int) -> Optional[str]:
        """Atomically pops the highest priority job from pending and moves it to running."""
        expire_score = time.time() + visibility_timeout
        job_id = pop_job_script(
            keys=[RedisQueue.PENDING_KEY, RedisQueue.RUNNING_KEY],
            args=[expire_score]
        )
        return job_id

    @staticmethod
    def remove_from_running(job_id: str) -> None:
        """Removes a job from the running queue."""
        redis_conn.zrem(RedisQueue.RUNNING_KEY, job_id)

    @staticmethod
    def add_to_retry(job_id: str, delay_seconds: int) -> None:
        """Adds a job to the retry queue with a delay timestamp."""
        retry_time = time.time() + delay_seconds
        redis_conn.zadd(RedisQueue.RETRY_KEY, {job_id: retry_time})

    @staticmethod
    def remove_from_retry(job_id: str) -> None:
        """Removes a job from the retry queue."""
        redis_conn.zrem(RedisQueue.RETRY_KEY, job_id)

    @staticmethod
    def cancel_job(job_id: str) -> None:
        """Removes a job from all Redis queues."""
        redis_conn.zrem(RedisQueue.PENDING_KEY, job_id)
        redis_conn.zrem(RedisQueue.RUNNING_KEY, job_id)
        redis_conn.zrem(RedisQueue.RETRY_KEY, job_id)

    @staticmethod
    def get_expired_running_jobs() -> List[str]:
        """Returns job IDs in running queue whose visibility timeout has expired."""
        now = time.time()
        return redis_conn.zrangebyscore(RedisQueue.RUNNING_KEY, "-inf", now)

    @staticmethod
    def get_expired_retry_jobs() -> List[str]:
        """Returns job IDs in retry queue whose backoff delay has expired."""
        now = time.time()
        return redis_conn.zrangebyscore(RedisQueue.RETRY_KEY, "-inf", now)

    @staticmethod
    def move_running_to_pending(job_id: str, priority: int) -> None:
        """Atomically removes a job from running and pushes it back to pending."""
        pipe = redis_conn.pipeline()
        pipe.zrem(RedisQueue.RUNNING_KEY, job_id)
        pipe.zadd(RedisQueue.PENDING_KEY, {job_id: priority})
        pipe.execute()

    @staticmethod
    def move_retry_to_pending(job_id: str, priority: int) -> None:
        """Atomically removes a job from retry and pushes it back to pending."""
        pipe = redis_conn.pipeline()
        pipe.zrem(RedisQueue.RETRY_KEY, job_id)
        pipe.zadd(RedisQueue.PENDING_KEY, {job_id: priority})
        pipe.execute()

    @staticmethod
    def acquire_leadership(scheduler_id: str, lease_ms: int) -> bool:
        """Attempts to acquire the leader lock."""
        return bool(redis_conn.set(RedisQueue.LEADER_KEY, scheduler_id, nx=True, px=lease_ms))

    @staticmethod
    def renew_leadership(scheduler_id: str, lease_ms: int) -> bool:
        """Attempts to renew the leader lock."""
        return bool(renew_leadership_script(keys=[RedisQueue.LEADER_KEY], args=[scheduler_id, lease_ms]))

    @staticmethod
    def get_leader() -> Optional[str]:
        """Returns the current leader scheduler ID."""
        return redis_conn.get(RedisQueue.LEADER_KEY)

    @staticmethod
    def get_queue_sizes() -> dict:
        """Returns size of pending, running and retry queues."""
        return {
            "pending": redis_conn.zcard(RedisQueue.PENDING_KEY),
            "running": redis_conn.zcard(RedisQueue.RUNNING_KEY),
            "retry": redis_conn.zcard(RedisQueue.RETRY_KEY)
        }
