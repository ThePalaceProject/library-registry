from flask import session
from flask_jwt_extended import verify_jwt_in_request
from functools import wraps
from library_registry.problem_details import (
    INVALID_CREDENTIALS,
)


def check_logged_in(fn):
    @wraps(fn)
    def decorated(*args, **kwargs):
        if session.get("username") or verify_jwt_in_request(optional=True):
            # 401 Unauthorized, username or password is incorrect
            return fn(*args, **kwargs)
        return INVALID_CREDENTIALS.response
    return decorated
