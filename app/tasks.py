import asyncio
import random
import logging

logger = logging.getLogger("worker")

async def execute_email_task(payload: dict) -> str:
    """Simulates sending an email."""
    to = payload.get("to", "recipient@example.com")
    subject = payload.get("subject", "Hello World")
    body = payload.get("body", "This is a test email.")
    fail = payload.get("fail", False)
    fail_probability = payload.get("fail_probability", 0.0)

    logger.info(f"Starting Email Task: sending to {to}...")
    await asyncio.sleep(2.0)  # Simulate network latency

    # Inject failure if requested
    if fail or (fail_probability > 0 and random.random() < fail_probability):
        logger.error(f"Email Task failed: SMTP Server connection refused for {to}")
        raise ConnectionError("SMTP server connection timed out.")

    logger.info(f"Email Task complete: sent to {to} successfully.")
    return f"Email sent successfully to {to}"


async def execute_video_task(payload: dict) -> str:
    """Simulates processing a video: compressing, thumbnail, and captions."""
    video_name = payload.get("video_name", "intro.mp4")
    resolution = payload.get("resolution", "1080p")
    steps = ["compressing", "generating_thumbnail", "creating_captions"]

    logger.info(f"Starting Video Processing Task: {video_name} at {resolution}")
    
    for step in steps:
        logger.info(f"Video Task [{video_name}]: Step '{step}' in progress...")
        await asyncio.sleep(2.0)  # Simulate CPU-heavy workload
        logger.info(f"Video Task [{video_name}]: Step '{step}' finished.")

    logger.info(f"Video Processing Task complete: {video_name} processed successfully.")
    return f"Processed {video_name} at resolution {resolution}"


async def execute_report_task(payload: dict) -> str:
    """Simulates generating a business report."""
    report_name = payload.get("report_name", "Q2_sales_report")
    filters = payload.get("filters", {})

    logger.info(f"Starting Report Generation: {report_name}...")
    await asyncio.sleep(3.0)  # Simulate database query and formatting
    
    logger.info(f"Report Generation complete: {report_name} saved to storage.")
    return f"Report {report_name} generated successfully with filters {filters}"


async def execute_fail_task(payload: dict) -> str:
    """A task designed to fail immediately for testing retries & DLQ."""
    logger.info("Executing Fail Task: intended failure starting...")
    await asyncio.sleep(0.5)
    raise RuntimeError("Intentional failure for testing retries and DLQ.")


# Map job types to functions
TASK_REGISTRY = {
    "email": execute_email_task,
    "video": execute_video_task,
    "report": execute_report_task,
    "fail_task": execute_fail_task
}
