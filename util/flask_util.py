"""Utilities for Flask applications."""
import re

import flask
from flask import Response

from . import problem_detail
from .language import languages_from_accept


_COMMA_SPACE_SEPARATOR = re.compile(r'\s*,\s*')


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
    addresses = re.split(_COMMA_SPACE_SEPARATOR, flask.request.headers.get('X-Forwarded-For', '').strip())
    return addresses[0] or flask.request.remote_addr
