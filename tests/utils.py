import io
import json
from typing import Any

from requests import Response


def mock_response(
    status_code: int, content: Any, headers=None, stream=False, **extras
) -> Response:
    """Create a mock response object"""
    r = Response()
    r.status_code = status_code
    r.headers = headers or {}
    if type(content) in [dict, list]:
        r._content = json.dumps(content)
    else:
        r._content = content

    for k, v in extras.items():
        setattr(r, k, v)

    if stream:
        if type(r._content) is str:
            r.raw = io.BytesIO(bytes(r._content))
        else:
            r.raw = io.BytesIO(r._content)

    return r
