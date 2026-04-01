"""
Gunicorn configuration for LogSight.

Upload requests return 202 Accepted immediately (parsing runs in a background
daemon thread), so a normal 30-second worker timeout is sufficient for the HTTP
layer.  The timeout below is set generously for slow networks on very large file
uploads (500 MB raw data at 10 MB/s ≈ 50 s transfer time).

Usage:
    gunicorn loganalytics.wsgi:application --config gunicorn.conf.py
"""

import multiprocessing

# ---------------------------------------------------------------------------
# Worker configuration
# ---------------------------------------------------------------------------

# sync workers are fine: each request returns quickly (202 for uploads).
worker_class = "sync"

# 2–4 × CPU cores is the standard recommendation for sync workers.
workers = multiprocessing.cpu_count() * 2 + 1

# Worker timeout in seconds.  Since uploads return 202 immediately and parsing
# runs in a background thread, 120 s is more than enough even for slow networks
# transferring a 500 MB file.
timeout = 120

# Graceful shutdown timeout: allow background threads to finish their current
# batch before the worker is killed.  Set to slightly less than timeout.
graceful_timeout = 90

# ---------------------------------------------------------------------------
# Connection / request limits
# ---------------------------------------------------------------------------

# Maximum allowed size of an HTTP request body (bytes).
# Must be at least as large as LOGS_MAX_UPLOAD_SIZE (500 MB) plus headers.
limit_request_line   = 8190          # max URL length (bytes)
limit_request_fields = 200

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
accesslog = "-"   # stdout
errorlog  = "-"   # stderr
loglevel  = "info"
