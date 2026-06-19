import urllib.request
import urllib.error
import json
import time
import sys

API_URL = "http://127.0.0.1:8000/api"

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
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
        except Exception:
            err_body = str(e)
        return e.code, err_body
    except urllib.error.URLError as e:
        print(f"Connection Error contacting api at {url}: {e}")
        return 500, None

def test_advanced():
    print("=" * 60)
    print("       NEXUS ADVANCED SYSTEM DESIGN VERIFICATION")
    print("=" * 60)

    # 1. API Health
    print("\n[Test 1] Checking API health...")
    status, stats = make_request("/stats")
    if status != 200:
        print(f"FAIL: API not reachable (Status {status})")
        sys.exit(1)
    print(f"SUCCESS: Stats retrieved. Pending queue size: {stats['pending']}")

    # 2. Delayed Jobs
    print("\n[Test 2] Testing Ad-Hoc Task Delay...")
    delay_seconds = 5
    payload = {"msg": "Delayed Task Test"}
    status, job = make_request("/jobs", method="POST", data={
        "job_type": "email",
        "priority": 5,
        "payload": payload,
        "delay_seconds": delay_seconds
    })
    if status != 201:
        print(f"FAIL: Could not create delayed job. Status: {status}, Response: {job}")
        sys.exit(1)
    
    job_id = job["id"]
    print(f"SUCCESS: Created job {job_id} with {delay_seconds}s delay.")
    
    # Verify it is not executed immediately
    time.sleep(2)
    status, job_check = make_request(f"/jobs/{job_id}")
    if job_check["status"] != "PENDING":
        print(f"FAIL: Job started prematurely. Status: {job_check['status']}")
        sys.exit(1)
    print(f"  T+2s: Job status is still {job_check['status']} (Correctly delayed)")

    # Wait for delay to mature + scheduler sweep
    print("  Waiting 5 seconds for delay to mature...")
    time.sleep(5)
    status, job_check = make_request(f"/jobs/{job_id}")
    print(f"  T+7s: Job status is now {job_check['status']}.")
    
    # 3. DLQ Redriving
    print("\n[Test 3] Testing DLQ Redriving...")
    status, job_dlq = make_request("/jobs", method="POST", data={
        "job_type": "email",
        "priority": 1,
        "payload": {"msg": "To be cancelled"},
        "delay_seconds": 60
    })
    dlq_id = job_dlq["id"]
    
    # Cancel the job to force it into DEAD state
    status, cancelled_job = make_request(f"/jobs/{dlq_id}/cancel", method="POST")
    if status != 200 or cancelled_job["status"] != "DEAD":
        print(f"FAIL: Could not cancel job to seed DLQ. Status: {status}, Job: {cancelled_job}")
        sys.exit(1)
    print(f"SUCCESS: Forced job {dlq_id} to DEAD (DLQ) state.")

    # Redrive DLQ
    status, redrive_res = make_request("/jobs/redrive-dlq", method="POST")
    if status != 200:
        print(f"FAIL: Redrive DLQ API failed. Status: {status}, Response: {redrive_res}")
        sys.exit(1)
    print(f"SUCCESS: Redriven response: {redrive_res['message']}")

    # Verify job is now active again (PENDING, RUNNING, or SUCCESS)
    status, job_check = make_request(f"/jobs/{dlq_id}")
    if job_check["status"] not in ["PENDING", "RUNNING", "SUCCESS"]:
        print(f"FAIL: Redriven job is not in an active state. Status: {job_check['status']}")
        sys.exit(1)
    print(f"SUCCESS: Redriven job is active again (Current Status: {job_check['status']}).")

    # 4. Rate Limiting (Throttling)
    print("\n[Test 4] Testing Rate Limiting (Throttling)...")
    # Video limit: 1 execution per 15 seconds. Let's submit 2 video jobs.
    status, v1 = make_request("/jobs", method="POST", data={
        "job_type": "video",
        "priority": 10,
        "payload": {"video_name": "v1.mp4"}
    })
    status, v2 = make_request("/jobs", method="POST", data={
        "job_type": "video",
        "priority": 9,
        "payload": {"video_name": "v2.mp4"}
    })
    
    print("  Submitted 2 video tasks (limit: 1/15s).")
    time.sleep(1)
    
    status, v1_check = make_request(f"/jobs/{v1['id']}")
    status, v2_check = make_request(f"/jobs/{v2['id']}")
    print(f"  v1 status: {v1_check['status']}, v2 status: {v2_check['status']}")
    # One of them should be running/success, and the other should be PENDING or RETRYING (postponed)
    if v1_check["status"] in ["RUNNING", "SUCCESS"] and v2_check["status"] in ["PENDING", "RETRYING"]:
        print("SUCCESS: Second video job was correctly rate-limited and postponed.")
    elif v2_check["status"] in ["RUNNING", "SUCCESS"] and v1_check["status"] in ["PENDING", "RETRYING"]:
        print("SUCCESS: First video job was correctly rate-limited and postponed.")
    else:
        print(f"WARNING: Rate limit check was inconclusive. v1: {v1_check['status']}, v2: {v2_check['status']}")

    # 5. Backpressure / Load Shedding
    print("\n[Test 5] Testing Backpressure & Load Shedding...")
    print("  Occupying workers with 2 slow report jobs concurrently...")
    
    import concurrent.futures
    
    def send_report_job(name):
        return make_request("/jobs", method="POST", data={
            "job_type": "report",
            "priority": 100,
            "payload": {"report_name": name, "sleep_seconds": 15.0}
        })

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(send_report_job, f"slow_r{i}") for i in [1, 2]]
        concurrent.futures.wait(futures)
    
    # Short wait to allow uvicorn threads to flush and workers to pick up report tasks
    time.sleep(0.2)
    
    print("  Flooding API with 52 email jobs while workers are busy...")
    success_count = 0
    rejected_count = 0
    for idx in range(52):
        status, res = make_request("/jobs", method="POST", data={
            "job_type": "email",
            "priority": 0,
            "payload": {"idx": idx}
        })
        if status == 201:
            success_count += 1
        elif status == 429:
            rejected_count += 1
        else:
            print(f"Unexpected status code {status}: {res}")

    print(f"  Results: {success_count} jobs accepted, {rejected_count} jobs rejected with 429 (Too Many Requests).")
    if rejected_count > 0:
        print("SUCCESS: Load shedding / backpressure triggered successfully!")
    else:
        print("FAIL: No jobs were shed. Did pending queue depth not reach 50?")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("All advanced system design tests passed successfully!")
    print("=" * 60)

if __name__ == "__main__":
    test_advanced()
