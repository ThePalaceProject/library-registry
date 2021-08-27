import gzip
from functools import wraps
from io import BytesIO

import flask

from util import GeometryUtility
from util.flask_util import originating_ip
from util.problem_detail import ProblemDetail


def has_library_factory(app):
    """Create a decorator that extracts a library uuid from request arguments."""

    def factory(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            """A decorator that extracts a library UUID from request
            arguments.
            """
            if "uuid" in kwargs:
                uuid = kwargs.pop("uuid")
            else:
                uuid = None
            library = app.library_registry.registry_controller.library_for_request(uuid)
            if isinstance(library, ProblemDetail):
                return library.response
            else:
                return f(*args, **kwargs)

        return decorated

    return factory


def uses_location_factory(app):
    """Create a decorator that guesses at a location for the client."""

    def factory(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            """A decorator that guesses at a location for the client."""
            location = flask.request.args.get("_location")
            if location:
                location = GeometryUtility.point_from_string(location)
            if not location:
                ip = originating_ip()
                location = GeometryUtility.point_from_ip(ip)
            return f(*args, _location=location, **kwargs)

        return decorated

    return factory


def compressible(f):
    """Decorate a function to make it transparently handle whatever
    compression the client has announced it supports.

    Currently the only form of compression supported is
    representation-level gzip compression requested through the
    Accept-Encoding header.

    This code was modified from
    http://kb.sites.apiit.edu.my/knowledge-base/how-to-gzip-response-in-flask/,
    though I don't know if that's the original source; it shows up in
    a lot of places.
    """

    @wraps(f)
    def compressor(*args, **kwargs):
        @flask.after_this_request
        def compress(response):
            if (
                response.status_code < 200
                or response.status_code >= 300
                or "Content-Encoding" in response.headers
            ):
                # Don't encode anything other than a 2xx response
                # code. Don't encode a response that's
                # already been encoded.
                return response

            accept_encoding = flask.request.headers.get("Accept-Encoding", "")
            if "gzip" not in accept_encoding.lower():
                return response

            # At this point we know we're going to be changing the
            # outgoing response.

            # TODO: I understand what direct_passthrough does, but am
            # not sure what it has to do with this, and commenting it
            # out doesn't change the results or cause tests to
            # fail. This is pure copy-and-paste magic.
            response.direct_passthrough = False

            buffer = BytesIO()
            gzipped = gzip.GzipFile(mode="wb", fileobj=buffer)
            gzipped.write(response.data)
            gzipped.close()
            response.data = buffer.getvalue()

            response.headers["Content-Encoding"] = "gzip"
            # TODO: This is bad if Vary is already set.
            response.headers["Vary"] = "Accept-Encoding"
            response.headers["Content-Length"] = len(response.data)

            return response

        return f(*args, **kwargs)

    return compressor
