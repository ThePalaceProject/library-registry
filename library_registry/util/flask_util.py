"""Utilities for Flask applications."""
import flask
from flask import Response

from library_registry.util import problem_detail
from library_registry.util.language import LanguageCodes


def problem_raw(type, status, title, detail=None, instance=None, headers={}):
    data = problem_detail.json(type, status, title, detail, instance)
    final_headers = {"Content-Type": problem_detail.JSON_MEDIA_TYPE}
    final_headers.update(headers)
    return status, final_headers, data


def problem(type, status, title, detail=None, instance=None, headers={}):
    """Create a Response that includes a Problem Detail Document."""
    status, headers, data = problem_raw(type, status, title, detail, instance, headers)
    return Response(data, status, headers)


def languages_for_request():
    return LanguageCodes.languages_from_accept(flask.request.accept_languages)


def originating_ip():
    """
    If there is an X-Forwarded-For header, use its value as the originating IP address.
    Otherwise, use the address that originated this request.
    """
    address = None
    header = 'X-Forwarded-For'

    if header in flask.request.headers:
        address = flask.request.headers[header]

    if not address:
        address = flask.request.remote_addr

    return address
