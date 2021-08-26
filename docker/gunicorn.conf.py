import base64
import hashlib
import multiprocessing
import os
from pathlib import Path


# The `LIBRARY_REGISTRY_DOCKER_HOME` environment variables is set in the
# `Dockerfile`. It contains the value of the app directory.
APP_HOME = os.environ.get("LIBRARY_REGISTRY_DOCKER_HOME")

# Shared Settings
wsgi_app = "app:app"
accesslog = "-"
errorlog = "-"
loglevel = "info"
limit_request_line = 4094   # max size of HTTP request line, in bytes
limit_request_fields = 100  # max number of header fields allowed in a request
limit_request_field_size = 8190  # allowed size of a single HTTP header field
preload_app = False         # defer app load till after worker start
chdir = APP_HOME  # change to this dir before loading apps
daemon = False              # Don't background the process
user = "nginx"
group = "nginx"
bind = ["127.0.0.1:8000"]     # listen on 8000, only on the loopback address
workers = (2 * multiprocessing.cpu_count()) + 1
threads = 2
pythonpath = ",".join(
    [
        APP_HOME,
    ]
)

# Env-Specific Settings

if os.environ.get('FLASK_ENV', None) == 'development':
    reload = True       # restart workers when app code changes
    loglevel = "debug"  # default loglevel is 'info'
    workers = 1         # single worker for local dev
