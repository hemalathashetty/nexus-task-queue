import asyncio
import random
import logging
import os
import time
import zipfile
import csv
import json
import smtplib
from email.mime.text import MIMEText
from app.database import SessionLocal
from app.models import Job

logger = logging.getLogger("worker")

def send_smtp_email(host, port, user, password, sender, to, subject, body):
    """Blocking SMTP email sender helper executed inside an async thread pool."""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=10) as server:
            server.login(user, password)
            server.sendmail(sender, [to], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(sender, [to], msg.as_string())


async def execute_email_task(payload: dict) -> str:
    """Sends a real SMTP email if credentials are set, else prints MIME output to stdout."""
    to = payload.get("to", "recipient@example.com")
    subject = payload.get("subject", "Nexus Task Execution Alert")
    body = payload.get("body", "This is a real-world task mail sent via the Nexus task queue!")
    fail_probability = payload.get("fail_probability", 0.0)

    # 1. Inject failure if requested (for testing retry/DLQ)
    if fail_probability > 0 and random.random() < fail_probability:
        logger.error(f"Email Task failed: SMTP Server connection refused for {to}")
        raise ConnectionError("SMTP server connection timed out.")

    # 2. Get SMTP configurations
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM", "nexus-task-queue@example.com")

    logger.info(f"Starting Email Task: sending to {to}...")

    # 3. If SMTP config is missing, simulate and print to console
    if not smtp_host or not smtp_user or not smtp_password:
        logger.warning("[SMTP Config Missing] Dumping formatted MIME message to console instead.")
        await asyncio.sleep(2.0)  # Maintain standard latency for testing/rate-limiting
        
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = to
        
        print("\n" + "=" * 50)
        print("          DUMMY SMTP TRANSMISSION LOG")
        print("=" * 50)
        print(msg.as_string())
        print("=" * 50 + "\n")
        
        return f"Email simulated successfully to {to}. Output printed to server logs."

    # 4. Perform real SMTP delivery
    try:
        await asyncio.to_thread(
            send_smtp_email, smtp_host, smtp_port, smtp_user, smtp_password, smtp_from, to, subject, body
        )
        return f"Email sent successfully via SMTP to {to}"
    except Exception as e:
        logger.error(f"SMTP sending failed: {str(e)}")
        raise RuntimeError(f"SMTP delivery failed: {str(e)}")


def zip_directory(src_dir, dest_zip_path):
    """Zips a directory recursively, skipping the exports subdirectory itself."""
    with zipfile.ZipFile(dest_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(src_dir):
            if "exports" in root:
                continue
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, os.path.dirname(src_dir))
                zipf.write(file_path, rel_path)


async def execute_video_task(payload: dict) -> str:
    """Performs real-world File Zip Backup Compression of the app/static folder."""
    video_name = payload.get("video_name", "static_backup.zip")
    logger.info(f"Starting File Compression Backup Task: zipping app/static -> app/static/exports/{video_name}")
    
    # Simulate CPU workload and delay
    await asyncio.sleep(2.0)
    
    dest_path = f"app/static/exports/{video_name}"
    
    try:
        await asyncio.to_thread(zip_directory, "app/static", dest_path)
        file_size = os.path.getsize(dest_path)
        logger.info(f"Compression Backup complete: {dest_path} ({file_size} bytes)")
        return f"Backup successful! Compressed app/static to /static/exports/{video_name} ({file_size} bytes)"
    except Exception as e:
        logger.error(f"Compression failed: {str(e)}")
        raise RuntimeError(f"Compression failed: {str(e)}")


async def execute_report_task(payload: dict) -> str:
    """Queries SQLite job records and exports the data to a downloadable CSV report file."""
    report_name = payload.get("report_name", "jobs_summary")
    sleep_seconds = payload.get("sleep_seconds", 3.0)

    logger.info(f"Starting Database CSV Export: {report_name}...")
    await asyncio.sleep(sleep_seconds)  # Simulate DB build latency
    
    # 1. Query SQLite jobs database
    db = SessionLocal()
    try:
        jobs = db.query(Job).order_by(Job.created_at.desc()).all()
    except Exception as e:
        logger.error(f"Failed to query database for report: {str(e)}")
        raise RuntimeError(f"Database query failed: {str(e)}")
    finally:
        db.close()

    # 2. Write rows to CSV file
    timestamp = int(time.time())
    filename = f"{report_name}_{timestamp}.csv"
    filepath = f"app/static/exports/{filename}"
    
    try:
        with open(filepath, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Job ID", "Job Type", "Status", "Priority", "Retry Count", "Created At", "Completed At", "Error Message", "Trace ID"])
            for job in jobs:
                writer.writerow([
                    job.id,
                    job.job_type,
                    job.status,
                    job.priority,
                    job.retry_count,
                    job.created_at.isoformat() if job.created_at else "",
                    job.completed_at.isoformat() if job.completed_at else "",
                    job.error_message or "",
                    job.trace_id or ""
                ])
        
        download_url = f"/static/exports/{filename}"
        logger.info(f"Report CSV Export complete: {filepath} ({len(jobs)} records)")
        
        # Return structured JSON containing download info
        return json.dumps({
            "message": f"Successfully compiled {len(jobs)} job history records to CSV.",
            "download_url": download_url,
            "total_records": len(jobs)
        })
    except Exception as e:
        logger.error(f"Failed to compile report: {str(e)}")
        raise RuntimeError(f"Report compilation failed: {str(e)}")


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
