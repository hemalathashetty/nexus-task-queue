from pydantic import BaseModel, Field
from typing import Optional, Any, Dict
from uuid import UUID
from datetime import datetime

class JobCreate(BaseModel):
    job_type: str = Field(..., example="email")
    payload: Dict[str, Any] = Field(default_factory=dict, example={"to": "user@example.com", "body": "hello"})
    priority: int = Field(default=0, description="Priority of the job. Higher values represent higher priority.")
    max_retries: int = Field(default=3, description="Maximum number of retries if the job fails.")
    delay_seconds: Optional[int] = Field(default=None, description="Delay in seconds before the job runs.")

class JobResponse(BaseModel):
    id: UUID
    job_type: str
    status: str
    priority: int
    retry_count: int
    max_retries: int
    payload: Dict[str, Any]
    error_message: Optional[str] = None
    worker_name: Optional[str] = None
    trace_id: UUID
    run_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class StatsResponse(BaseModel):
    total: int
    pending: int
    running: int
    success: int
    failed: int
    retrying: int
    dead: int
    leader: Optional[str] = None
