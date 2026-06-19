// Active Status Filter & Cached Statistics State
let activeFilter = 'all';
let previousStats = null;

// DOM Elements
const statsElements = {
    total: document.getElementById('stat-total'),
    pending: document.getElementById('stat-pending'),
    running: document.getElementById('stat-running'),
    retrying: document.getElementById('stat-retrying'),
    success: document.getElementById('stat-success'),
    dead: document.getElementById('stat-dead')
};

const jobForm = document.getElementById('job-form');
const seedBtn = document.getElementById('seed-btn');
const redriveBtn = document.getElementById('redrive-btn');
const delaySecondsInput = document.getElementById('delay_seconds');
const jobsTbody = document.getElementById('jobs-tbody');
const filterBtns = document.querySelectorAll('.filter-btn');
const payloadTextarea = document.getElementById('payload');
const jobTypeSelect = document.getElementById('job_type');
const leaderElement = document.getElementById('active-leader');
const toastContainer = document.getElementById('toast-container');

// Default Payload Templates
const payloadTemplates = {
    email: JSON.stringify({ to: "user@example.com", subject: "Nexus Alert", body: "A background task has executed successfully on the Nexus engine." }, null, 2),
    video: JSON.stringify({ video_name: "nexus_demo.mp4", resolution: "1080p" }, null, 2),
    report: JSON.stringify({ report_name: "nexus_audit_2026", filters: { quarter: "Q2", department: "Engineering" } }, null, 2),
    fail_task: JSON.stringify({ reason: "Testing Nexus Dead Letter Queue" }, null, 2)
};

// Toast Notification Engine
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    // Icon selection based on type
    let iconSvg = '';
    if (type === 'success') {
        iconSvg = `<svg viewBox="0 0 24 24" width="16" height="16" stroke="var(--color-green)" stroke-width="2.5" fill="none"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`;
    } else if (type === 'error') {
        iconSvg = `<svg viewBox="0 0 24 24" width="16" height="16" stroke="var(--color-red)" stroke-width="2.5" fill="none"><polygon points="7.86 2 16.14 2 22 7.86 22 16.14 16.14 22 7.86 22 2 16.14 2 7.86 7.86 2"></polygon><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`;
    } else {
        iconSvg = `<svg viewBox="0 0 24 24" width="16" height="16" stroke="var(--color-blue)" stroke-width="2.5" fill="none"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>`;
    }

    toast.innerHTML = `
        ${iconSvg}
        <span class="toast-message">${message}</span>
    `;

    toastContainer.appendChild(toast);

    // Fade out and remove after 4 seconds
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        toast.style.transition = 'all 0.4s ease';
        setTimeout(() => toast.remove(), 400);
    }, 3600);
}

// Update payload textarea when job type changes
jobTypeSelect.addEventListener('change', (e) => {
    const type = e.target.value;
    if (payloadTemplates[type]) {
        payloadTextarea.value = payloadTemplates[type];
    }
});

// Setup Filter Buttons
filterBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        filterBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activeFilter = btn.getAttribute('data-status');
        fetchJobs(); // Update table with active filter
    });
});

// Fetch Stats Summary
async function fetchStats() {
    try {
        const res = await fetch('/api/stats');
        if (!res.ok) throw new Error("Failed to fetch stats");
        const stats = await res.json();
        
        // Update DOM stats counters
        statsElements.total.textContent = stats.total;
        statsElements.pending.textContent = stats.pending;
        statsElements.running.textContent = stats.running;
        statsElements.retrying.textContent = stats.retrying;
        statsElements.success.textContent = stats.success;
        statsElements.dead.textContent = stats.dead;

        // Update Leader display
        if (stats.leader) {
            leaderElement.textContent = `Leader: ${stats.leader}`;
            leaderElement.parentElement.classList.add('active');
        } else {
            leaderElement.textContent = "Leader: None";
            leaderElement.parentElement.classList.remove('active');
        }

        // Compare state changes to trigger premium Toasts
        if (previousStats !== null) {
            if (stats.success > previousStats.success) {
                const count = stats.success - previousStats.success;
                showToast(`${count} task${count > 1 ? 's' : ''} completed successfully!`, 'success');
            }
            if (stats.dead > previousStats.dead) {
                const count = stats.dead - previousStats.dead;
                showToast(`${count} task${count > 1 ? 's' : ''} failed and moved to DLQ.`, 'error');
            }
            if (stats.running > previousStats.running && stats.pending < previousStats.pending) {
                showToast("Worker pool picked up new tasks for execution.", "info");
            }
        }
        previousStats = stats;
        
    } catch (err) {
        console.error("Stats fetch error:", err);
    }
}

// Fetch and Render Jobs Log
async function fetchJobs() {
    try {
        const url = activeFilter === 'all' ? '/api/jobs' : `/api/jobs?status=${activeFilter}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error("Failed to fetch jobs list");
        const jobs = await res.json();

        // Clear log
        jobsTbody.innerHTML = '';

        if (jobs.length === 0) {
            jobsTbody.innerHTML = `
                <tr>
                    <td colspan="9" class="empty-state">No jobs found under status '${activeFilter}'.</td>
                </tr>
            `;
            return;
        }

        jobs.forEach(job => {
            const tr = document.createElement('tr');
            
            // Format details/payload info
            let detailsText = '';
            if (job.status === 'SUCCESS') {
                detailsText = `<span class="success-text">Executed successfully.</span>`;
            } else if (job.status === 'DEAD' || job.status === 'FAILED' || job.status === 'RETRYING') {
                detailsText = `<span class="error-text" title="${job.error_message || ''}">${job.error_message || 'Unknown error'}</span>`;
            } else if (job.status === 'RUNNING') {
                detailsText = `<span class="mono" style="color: var(--color-orange)">Processing steps...</span>`;
            } else if (job.status === 'PENDING' && job.run_at) {
                const runAtTime = new Date(job.run_at);
                if (runAtTime > new Date()) {
                    detailsText = `<span class="mono" style="color: var(--color-purple)">Delayed until ${runAtTime.toLocaleTimeString()}</span>`;
                } else {
                    detailsText = `<span class="mono" style="font-size: 0.75rem">${JSON.stringify(job.payload)}</span>`;
                }
            } else {
                detailsText = `<span class="mono" style="font-size: 0.75rem">${JSON.stringify(job.payload)}</span>`;
            }

            // Cancel Action Button
            const canCancel = (job.status === 'PENDING' || job.status === 'RETRYING');
            const actionCell = canCancel 
                ? `<button class="action-btn" onclick="cancelJob('${job.id}')">Cancel</button>` 
                : `<span class="mono" style="opacity: 0.5">-</span>`;

            // Shorten ID and Trace ID for UX
            const shortId = job.id.substring(0, 8);
            const shortTraceId = job.trace_id ? job.trace_id.substring(0, 8) : '-';

            tr.innerHTML = `
                <td class="mono" title="${job.id}">${shortId}...</td>
                <td class="mono" title="${job.trace_id || ''}">${shortTraceId}</td>
                <td style="font-weight: 600; text-transform: capitalize;">${job.job_type}</td>
                <td class="mono" style="font-weight: bold; color: ${job.priority > 0 ? 'var(--color-orange)' : 'inherit'}">${job.priority}</td>
                <td><span class="status-pill ${job.status.toLowerCase()}">${job.status}</span></td>
                <td class="mono">${job.retry_count} / ${job.max_retries}</td>
                <td class="mono">${job.worker_name || '-'}</td>
                <td>${detailsText}</td>
                <td>${actionCell}</td>
            `;

            jobsTbody.appendChild(tr);
        });

    } catch (err) {
        console.error("Jobs fetch error:", err);
    }
}

// Cancel Job Action
async function cancelJob(jobId) {
    if (!confirm("Are you sure you want to cancel this job?")) return;
    try {
        const res = await fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
        if (!res.ok) {
            const errData = await res.json();
            showToast(`Cancel failed: ${errData.detail}`, 'error');
            return;
        }
        showToast("Job was successfully cancelled and removed from the active queue.", "info");
        fetchStats();
        fetchJobs();
    } catch (err) {
        console.error("Cancel job error:", err);
    }
}

// Form Submission
jobForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const jobType = document.getElementById('job_type').value;
    const priority = parseInt(document.getElementById('priority').value, 10);
    const delaySecondsVal = delaySecondsInput ? parseInt(delaySecondsInput.value, 10) : 0;
    const payloadStr = payloadTextarea.value;
    
    let payload = {};
    try {
        payload = JSON.parse(payloadStr);
    } catch (err) {
        showToast("Invalid JSON payload format.", 'error');
        return;
    }

    try {
        const reqData = {
            job_type: jobType,
            priority: priority,
            payload: payload
        };
        if (delaySecondsVal > 0) {
            reqData.delay_seconds = delaySecondsVal;
        }

        const res = await fetch('/api/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(reqData)
        });

        if (!res.ok) {
            const errData = await res.json();
            if (res.status === 429) {
                showToast(`⚠️ Load Shedding: ${errData.detail || "Queue capacity limit reached (50 pending tasks). Please try again later."}`, 'error');
                return;
            }
            throw new Error(errData.detail || "Failed to enqueue job");
        }

        // Reset form slightly
        document.getElementById('priority').value = '0';
        if (delaySecondsInput) delaySecondsInput.value = '0';
        showToast("New job dispatched successfully.", 'success');
        
        // Refresh UI
        fetchStats();
        fetchJobs();
    } catch (err) {
        showToast(`Enqueue failed: ${err.message}`, 'error');
    }
});

// Seed Batch Button
seedBtn.addEventListener('click', async () => {
    seedBtn.disabled = true;
    seedBtn.textContent = "Seeding...";
    try {
        const res = await fetch('/api/jobs/seed-test', { method: 'POST' });
        if (!res.ok) throw new Error("Failed to seed database");
        
        showToast("Simulation batch enqueued successfully.", 'success');
        // Refresh UI
        fetchStats();
        fetchJobs();
    } catch (err) {
        showToast(`Seeding failed: ${err.message}`, 'error');
    } finally {
        seedBtn.disabled = false;
        seedBtn.innerHTML = `
            <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path></svg>
            Seed Simulation
        `;
    }
});

// DLQ Redrive Button
if (redriveBtn) {
    redriveBtn.addEventListener('click', async () => {
        redriveBtn.disabled = true;
        redriveBtn.textContent = "Redriving...";
        try {
            const res = await fetch('/api/jobs/redrive-dlq', { method: 'POST' });
            if (!res.ok) throw new Error("Failed to redrive dead jobs");
            const data = await res.json();
            showToast(data.message || "DLQ jobs successfully redriven.", 'success');
            fetchStats();
            fetchJobs();
        } catch (err) {
            showToast(`Redrive failed: ${err.message}`, 'error');
        } finally {
            redriveBtn.disabled = false;
            redriveBtn.innerHTML = `
                <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
                Re-drive DLQ
            `;
        }
    });
}

// Initialize & Set Loop Intervals
fetchStats();
fetchJobs();
setInterval(() => {
    fetchStats();
    fetchJobs();
}, 1500);
