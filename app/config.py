import os

class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./jobs.db")
    REDIS_URL: str = os.getenv("REDIS_URL", "mock")
    VISIBILITY_TIMEOUT: int = int(os.getenv("VISIBILITY_TIMEOUT", "20"))  # in seconds
    WORKER_NAME: str = os.getenv("WORKER_NAME", "worker-default")
    SCHEDULER_ID: str = os.getenv("SCHEDULER_ID", "scheduler-default")

settings = Settings()
