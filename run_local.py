import asyncio
import threading
import uvicorn
import logging
import sys
from app.worker import worker_loop
from app.scheduler import scheduler_loop

# Configure system root log to output cleanly
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

def run_api_server():
    """Runs the FastAPI uvicorn server in a separate thread."""
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="warning")

async def main():
    # 1. Start the API/Dashboard Server in a background thread
    api_thread = threading.Thread(target=run_api_server, daemon=True)
    api_thread.start()
    
    # 2. Wait briefly for uvicorn to initialize
    await asyncio.sleep(1.5)
    
    print("\n" + "=" * 80)
    print("      NEXUS JOB SCHEDULER - LOCAL TASK ENGINE ACTIVE")
    print("      - Control Dashboard: http://127.0.0.1:8000/")
    print("      - Active Worker Pool: worker-1, worker-2 (running parallel)")
    print("      - Schedulers: scheduler-1, scheduler-2 (competing for leadership)")
    print("      - Database: SQLite (local file 'jobs.db')")
    print("      - Broker: Fakeredis (shared in-memory queue & locks)")
    print("      Press Ctrl+C to stop all services.")
    print("=" * 80 + "\n")

    # 3. Gather background loops in the asyncio event loop
    try:
        await asyncio.gather(
            worker_loop("worker-1"),
            worker_loop("worker-2"),
            scheduler_loop("scheduler-1"),
            scheduler_loop("scheduler-2")
        )
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down local task cluster. Exiting safely...")
