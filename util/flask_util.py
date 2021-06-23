"""Utilities for Flask applications."""

import flask
from flask import Response

from . import problem_detail
from .language import languages_from_accept


def problem_raw(type, status, title, detail=None, instance=None, headers={}):
    data = problem_detail.json(type, status, title, detail, instance)
    final_headers = { "Content-Type" : problem_detail.JSON_MEDIA_TYPE }
    final_headers.update(headers)
    return status, final_headers, data

def problem(type, status, title, detail=None, instance=None, headers={}):
    """Create a Response that includes a Problem Detail Document."""
    status, headers, data = problem_raw(
        type, status, title, detail, instance, headers)
    return Response(data, status, headers)
    
def languages_for_request():
    return languages_from_accept(flask.request.accept_languages)

def originating_ip() -> str:
    """Determine the client's IP address.

    If there is an X-Forwarded-For header and it has a non-empty value,
    use the client value as the originating IP address. Otherwise, use
    the address that originated this request.

    NB: The format of an X-Forwarded-For header value is: "<client-ip>, [proxy1-ip, [proxy2ip ...]]

    :return: IP address of request originator
    :rtype: str
    """
    forwarded_for_client_ip = next(map(str.strip, flask.request.headers.get('X-Forwarded-For', '').split(',')))
    return forwarded_for_client_ip or flask.request.remote_addr
