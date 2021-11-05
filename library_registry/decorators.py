from functools import wraps
from io import BytesIO
import gzip

from flask import jsonify, Response, g, request
from flask_sqlalchemy_session import current_session

from library_registry.model import Library
from library_registry.util import GeometryUtility
from library_registry.util.flask_util import originating_ip
from library_registry.util.geo import Location, InvalidLocationException
from library_registry.util.problem_detail import ProblemDetail
from library_registry.problem_details import LIBRARY_NOT_FOUND


def deprecated_route(f):
    """Report usage of a deprecated route"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # TODO: Log the usage of a deprecated route and emit a metric

        return f(*args, **kwargs)

    return decorated


def uses_location(f):
    """
    Attempts to guess a location for a request, based on either:
        - A '_location' string value in request.args, formatted as '<lat>,<long>'
        - Failing that, the originating IP address of the request, from either
            - The value of an 'X-Forwarded-For' header (expected from Nginx)
            - Failing that, the value of request.remote_addr

    Adds the found location (or None) to g.location as a geometry string
        (ex: 'SRID=4326;POINT(74.006 40.7128)')
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        raw_location = request.args.get("_location", None)
        location_obj = None

        if raw_location:            # See if what we got in args creates a valid location
            try:
                location_obj = Location(raw_location)
            except InvalidLocationException:
                pass

        if not location_obj:        # Try getting a location off the client's IP
            try:
                location_obj = Location(GeometryUtility.point_from_ip(originating_ip()))
            except InvalidLocationException:
                pass

        g.location = location_obj

        return f(*args, **kwargs)

    return decorated


def has_library(f):
    """
    Places a Library instance into g.library, based on a uuid URL parameter.

    The uuid maps to Library.internal_urn, and may be in the following formats:
        - a UUID as output by str(uuid.uuid4())
        - a string beginning with "urn:uuid:" followed by a stringified uuid4() value
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        uuid_string = kwargs.pop("uuid", None)
        if not uuid_string:
            return LIBRARY_NOT_FOUND.response

        if not uuid_string.startswith("urn:uuid:"):
            uuid_string = "urn:uuid:" + uuid_string

        library = Library.for_urn(current_session, uuid_string)

        if not library:
            return LIBRARY_NOT_FOUND.response

        g.library = library

        return f(*args, **kwargs)

    return decorated


def returns_problem_detail(f):
    """
    Allows a view function, on error, to return a specific ProblemDetail instance,
    which will be rendered into a flask Response object.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        v = f(*args, **kwargs)

        if isinstance(v, ProblemDetail):
            return v.response

        return v

    return decorated


def returns_json_or_response_or_problem_detail(f):
    """
    Provides a usable Response for view functions which return any of:
        - A Python data structure (will be run through flask.jsonify())
        - A ProblemDetail instance
        - An instance of flask.Response or a subclass thereof
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        v = f(*args, **kwargs)

        if isinstance(v, ProblemDetail):
            return v.response

        if isinstance(v, Response):
            return v

        return jsonify(**v)

    return decorated


def compressible(f):
    """
    Compress the outgoing response payload using gzip if:
        * The response being rendered by the view is not a ProblemDetail, etc.
        * The request included an 'Accept-Encoding' header whose value includes 'gzip'
        * The response being rendered by the view carries a 2xx status code
        * The response body is not already explicitly encoded
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        response = f(*args, **kwargs)
        accept_encoding = request.headers.get('Accept-Encoding', '')

        if (                                                # Reasons to exit early, without compressing:
            not isinstance(response, Response) or           # - We don't want to compress non-Response data
            'gzip' not in accept_encoding.lower() or        # - They didn't ask for compression
            not (199 < response.status_code < 300) or       # - It's not a 2xx response--we only compress 2xx
            'Content-Encoding' in response.headers          # - It's already been encoded, don't mess with it
        ):
            return response

        # Perform the compression on the response data
        buffer = BytesIO()
        with gzip.GzipFile(mode='wb', fileobj=buffer) as gzipped:
            gzipped.write(response.data)
        response.data = buffer.getvalue()

        response.direct_passthrough = False  # This skips some Werkzeug/Flask checks, unneeded for a binary payload.
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Vary'] = 'Accept-Encoding'    # TODO: This is bad if Vary is already set.
        response.headers['Content-Length'] = len(response.data)

        return response

    return decorated
