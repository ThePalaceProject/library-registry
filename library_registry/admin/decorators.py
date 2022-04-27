from flask import session
from functools import wraps
from library_registry.problem_details import (
    INVALID_CREDENTIALS,
)

def check_logged_in(fn):
    @wraps(fn)
    def decorated(*args, **kwargs):
        if not session.get("username"):
            return INVALID_CREDENTIALS.response # 401 Unauthorized, username or password is incorrect
        return fn(*args, **kwargs)
    return decorated