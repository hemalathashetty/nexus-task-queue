import asyncio
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

async def main():
    # 1. Initialize Uvicorn Config and Server inside the same asyncio loop
    config = uvicorn.Config("app.main:app", host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    
    print("\n" + "=" * 80)
    print("      NEXUS JOB SCHEDULER - LOCAL TASK ENGINE ACTIVE")
    print("      - Control Dashboard: http://127.0.0.1:8000/")
    print("      - Active Worker Pool: worker-1, worker-2 (running parallel)")
    print("      - Schedulers: scheduler-1, scheduler-2 (competing for leadership)")
    print("      - Database: SQLite (local file 'jobs.db')")
    print("      - Broker: Fakeredis (shared in-memory queue & locks)")
    print("      Press Ctrl+C to stop all services.")
    print("=" * 80 + "\n")

    # 2. Gather background loops in the asyncio event loop
    try:
        await asyncio.gather(
            server.serve(),
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
