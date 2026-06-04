"""Utilities for Flask applications."""

import ipaddress
import re
from datetime import datetime
from functools import wraps
from urllib.parse import urljoin

from flask import Response, make_response, request
from werkzeug.http import http_date

from palace.registry.util import problem_detail
from palace.registry.util.language import languages_from_accept

IPV4_REGEX = re.compile(
    r"""(?<![0-9])                # Preceding character if any may not be numeric
                            (?P<address>              # entire address, capturing
                            (?:                       # quads 1-3 and separator, non-capturing
                                (?:                   # quad value, non-capturing
                                    [0-9]         |   # single digit   0-9
                                    [1-9][0-9]    |   # double digit  10-99
                                    1[0-9]{2}     |   # triple digit 100-199
                                    2[0-4][0-9]   |   # triple digit 200-249
                                    25[0-5]           # triple digit 250-255
                                )\.                   # dot separator
                            ){3}
                            (?:                       # quad 4, non-capturing
                                25[0-5]           |   # triple digit 250-255
                                2[0-4][0-9]       |   # triple digit 200-249
                                1[0-9]{2}         |   # triple digit 100-199
                                [1-9][0-9]        |   # double digit  10-99
                                [0-9]                 # single digit   0-9
                            )
                          )
                          (?![0-9])                   # trailing character if any may not be numeric
                          """,
    re.VERBOSE,
)


def deprecated(
    *,
    deprecation_date: datetime | None = None,
    sunset: datetime | None = None,
    documentation: str | None = None,
    replacement: str | None = None,
):
    """Mark a route as deprecated, injecting standardized HTTP response headers.

    The approach follows the mapping proposed in the OpenAPI discussion
    "proposal:OpenAPI 3.3 Proposal: API-Level Deprecation & Sunset Support":
    https://github.com/OAI/OpenAPI-Specification/discussions/5193

    ``Deprecation: true`` is always emitted; when ``deprecation_date`` is
    provided its value is used instead of the boolean.

    :param deprecation_date: When the endpoint was (or will be) deprecated.
        Emitted as ``Deprecation: <IMF-fixdate>`` (RFC 9745 §2).
    :param sunset: Date after which the endpoint may be removed.
        Emitted as ``Sunset: <HTTP-date>`` (RFC 8594).
    :param documentation: URL of a resource describing the deprecation context.
        Emitted as ``Link: <url>; rel="deprecation"`` (RFC 9745 §3.1).
    :param replacement: URL of the replacement endpoint.
        Emitted as ``Link: <url>; rel="successor-version"`` (RFC 5829).
    """
    if deprecation_date is not None and deprecation_date.tzinfo is None:
        raise ValueError("deprecation_date must be timezone-aware")
    if sunset is not None and sunset.tzinfo is None:
        raise ValueError("sunset must be timezone-aware")

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            response = make_response(f(*args, **kwargs))
            response.headers["Deprecation"] = (
                http_date(deprecation_date) if deprecation_date else "true"
            )
            if sunset is not None:
                response.headers["Sunset"] = http_date(sunset)
            link_parts = []
            if documentation is not None:
                link_parts.append(
                    f'<{urljoin(request.host_url, documentation)}>; rel="deprecation"'
                )
            if replacement is not None:
                link_parts.append(
                    f'<{urljoin(request.host_url, replacement)}>; rel="successor-version"'
                )
            if link_parts:
                response.headers.add("Link", ", ".join(link_parts))
            return response

        return wrapper

    return decorator


def problem_raw(type, status, title, detail=None, instance=None, headers=None):
    headers = headers or {}
    data = problem_detail.json(type, status, title, detail, instance)
    final_headers = {"Content-Type": problem_detail.JSON_MEDIA_TYPE}
    final_headers.update(headers)
    return status, final_headers, data


def problem(type, status, title, detail=None, instance=None, headers=None):
    """Create a Response that includes a Problem Detail Document."""
    headers = headers or {}
    status, headers, data = problem_raw(type, status, title, detail, instance, headers)
    return Response(data, status, headers)


def languages_for_request():
    return languages_from_accept(request.accept_languages)


def is_public_ipv4_address(ip_string):
    """Whether a given IPv4 address (either string or ipaddress.IPv4Address obj) is publicly routable"""
    if not isinstance(ip_string, ipaddress.IPv4Address):
        try:
            ip_string = ipaddress.ip_address(ip_string)
        except ValueError:
            pass  # TODO: Log it. Some caller is passing bad values to this fn.
            return None  # incoming value couldn't be coerced to an IPv4Address object

    return bool(
        ip_string.is_private is False
        and ip_string.is_multicast is False
        and ip_string.is_unspecified is False
        and ip_string.is_reserved is False
        and ip_string.is_loopback is False
        and ip_string.is_link_local is False
    )


def originating_ip():
    """
    Attempt to derive the client's IPv4 address from the flask.request object.

    Looks first at the X-Forwarded-For header, checking for multiple values
    (which can happen with multiple layers of proxy server). If no valid, public
    IP address is found there, looks at the request.remote_addr value.

    The format of an X-Forwarded-For header value is: "<client-ip>, [proxy1-ip, [proxy2ip ...]]
    If X-Forwarded-For contains more than one IP, we return the first public IP
    since the proxy servers will almost certainly be appending their IP to the end.

    If no valid, public IPv4 address is found in either location, returns None.
    """
    forwarded_for = request.headers.get("X-Forwarded-For", None)

    if not forwarded_for and not request.remote_addr:
        return None  # Nothing to go on from either headers or remote_addr

    client_ip = None

    if forwarded_for:
        try:
            fwd4_addresses = re.findall(IPV4_REGEX, forwarded_for)
        except TypeError:  # whatever's in the header isn't a string/bytes-like object
            pass

        for ip in fwd4_addresses:
            if is_public_ipv4_address(ip):
                client_ip = ip
                break

    # If we don't have an IP from the forwarded header, try getting it from remote_addr
    if not client_ip and request.remote_addr:
        if is_public_ipv4_address(request.remote_addr):
            client_ip = request.remote_addr

    return client_ip
