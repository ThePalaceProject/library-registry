import base64
import hashlib
import multiprocessing
import os
from pathlib import Path


def _venv_name(app_home, file='Pipfile'):
    """Compute the virtual environment name, given an app's directory."""
    app_home = os.path.normpath(app_home)
    filepath = os.path.join(app_home, file)
    hash = base64.urlsafe_b64encode(hashlib.sha256(filepath.encode()).digest()[:6]).decode()[:8]
    last_path_component = os.path.split(app_home)[-1]
    return f'{last_path_component}-{hash}'

# VENV_BASE_DIR = Path("/simplye_venv")
# VENV_ACTUAL = [Path(VENV_BASE_DIR / d) for d
#                in os.listdir(VENV_BASE_DIR)
#                if d.startswith("simplye_app-") and os.path.isdir(VENV_BASE_DIR / d)][0]

VENV_BASE = os.environ.get('WORKON_HOME', '/venv')
APP_HOME = os.environ.get('LIBRARY_REGISTRY_HOME', '/apps/library-registry')
APP_VENV = os.path.join(VENV_BASE, os.environ.get('LIBRARY_REGISTRY_VENV', _venv_name(APP_HOME)))

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
pythonpath = ",".join([
    APP_VENV,
    APP_HOME,
])

# Env-Specific Settings

if os.environ.get('FLASK_ENV', None) == 'development':
    reload = True       # restart workers when app code changes
    loglevel = "debug"  # default loglevel is 'info'
    workers = 1         # single worker for local dev
