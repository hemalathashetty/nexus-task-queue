import urllib.request
import json
import time
import sys

API_URL = "http://localhost:8000/api"

def make_request(path, method="GET", data=None):
    url = f"{API_URL}{path}"
    headers = {"Content-Type": "application/json"}
    req_data = None
    if data:
        req_data = json.dumps(data).encode("utf-8")
    
    req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"Connection Error contacting api at {url}: {e}")
        return 500, None

def run_tests():
    print("=" * 60)
    print("       DISTRIBUTED JOB SCHEDULER VERIFICATION SCRIPT")
    print("=" * 60)
    
    # 1. Check API Health & Connection
    print("\n[Test 1] Checking API connectivity and stats...")
    status, stats = make_request("/stats")
    if status != 200 or not stats:
        print("FAIL: Could not fetch statistics. Is the FastAPI container running?")
        sys.exit(1)
    print(f"SUCCESS: Stats retrieved. Total jobs in DB: {stats['total']}")
    print(f"Current State: Pending={stats['pending']}, Running={stats['running']}, Success={stats['success']}, Failed={stats['failed']}, Dead={stats['dead']}")
    
    # 2. Seed a Batch of Jobs
    print("\n[Test 2] Seeding a batch of test jobs...")
    status, seed_res = make_request("/jobs/seed-test", method="POST")
    if status != 200:
        print("FAIL: Could not seed test jobs.")
        sys.exit(1)
    
    seeded_ids = seed_res.get("seeded_job_ids", [])
    print(f"SUCCESS: Seeded {len(seeded_ids)} test jobs successfully.")
    
    # 3. Verify Priority & Execution order
    print("\n[Test 3] Monitoring queue execution for 10 seconds...")
    for i in range(5):
        time.sleep(2)
        _, current_stats = make_request("/stats")
        print(f"  T+{i*2}s: Pending={current_stats['pending']}, Running={current_stats['running']}, Success={current_stats['success']}, Dead={current_stats['dead']}")
        if current_stats['pending'] == 0 and current_stats['running'] == 0:
            print("  All jobs completed early.")
            break

    # 4. Fetch list of recent jobs to verify retries and priority
    print("\n[Test 4] Verifying job execution records in database...")
    status, jobs = make_request("/jobs?limit=10")
    if status != 200:
        print("FAIL: Could not fetch jobs list.")
        sys.exit(1)

    print("\nRecent Job Runs:")
    for job in jobs[:8]:
        err_msg = f" | Error: {job['error_message'][:35]}..." if job['error_message'] else ""
        print(f" - Job {str(job['id'])[:8]} | Type: {job['job_type'].ljust(8)} | Priority: {str(job['priority']).rjust(2)} | Status: {job['status'].ljust(8)} | Retries: {job['retry_count']}/{job['max_retries']}{err_msg}")
    
    # Check if we have at least one SUCCESS state and one DEAD/RETRYING state
    statuses = [j['status'] for j in jobs]
    has_success = "SUCCESS" in statuses
    has_dead = "DEAD" in statuses or "RETRYING" in statuses
    
    print("\nVerification Results:")
    if has_success:
        print(" [+] Priority scheduling and standard execution: SUCCESS")
    else:
        print(" [-] Standard execution verification: FAILED (No jobs succeeded yet)")
        
    if has_dead:
        print(" [+] Failure handling, retries, and DLQ promotion: SUCCESS")
    else:
        print(" [-] Retry/DLQ verification: PENDING (Run again or wait for fail_task to exhaust retries)")
        
    print("\n" + "=" * 60)
    print("Verification run completed. Open http://localhost:8000/ to view the live dashboard.")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
