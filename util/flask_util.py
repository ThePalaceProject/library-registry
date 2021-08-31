"""Utilities for Flask applications."""
import ipaddress
import re

from flask import Response, request

from . import problem_detail
from .language import languages_from_accept

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
