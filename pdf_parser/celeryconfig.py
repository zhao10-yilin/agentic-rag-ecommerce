"""Celery configuration for the PDF parser task queue.

Environment variables
---------------------
CELERY_BROKER_URL
    Message broker URI (default: ``redis://localhost:6379/0``).
    For local testing without Redis, set ``memory://``.
CELERY_RESULT_BACKEND
    Result backend URI (default: same as broker).
PDF_PARSER_MAX_MEMORY_MB
    Per-worker memory limit passed through to the orchestrator.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Broker & backend
# ---------------------------------------------------------------------------

broker_url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.environ.get("CELERY_RESULT_BACKEND", broker_url)

# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

task_serializer = "json"
accept_content = ["json"]
result_serializer = "json"

# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------

timezone = "UTC"
enable_utc = True

# ---------------------------------------------------------------------------
# Task execution semantics
# ---------------------------------------------------------------------------

task_track_started = True
task_time_limit = 600  # 10 min hard kill
task_soft_time_limit = 540  # 9 min soft limit (can be caught)

# ---------------------------------------------------------------------------
# Worker behaviour
# ---------------------------------------------------------------------------

# Do not prefetch more tasks than the worker can execute immediately.
# This keeps tasks in the queue where they are visible to monitoring tools
# rather than hidden inside a worker process.
worker_prefetch_multiplier = 1

# Restart the worker process after every task.  This prevents C / CUDA
# memory leaks from accumulating across long-running workers.
worker_max_tasks_per_child = 1
