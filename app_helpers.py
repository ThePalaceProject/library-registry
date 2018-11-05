import flask
from functools import wraps
from nose.tools import set_trace
from util import GeometryUtility
from util.problem_detail import ProblemDetail
from util.flask_util import originating_ip

def has_library_factory(app):
    """Create a decorator that extracts a library uuid from request arguments.
    """
    def factory(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            """A decorator that extracts a library UUID from request
            arguments.
            """
            if 'uuid' in kwargs:
                uuid = kwargs.pop("uuid")
            else:
                uuid = None
            library = app.library_registry.registry_controller.library_for_request(
                uuid
            )
            if isinstance(library, ProblemDetail):
                return library.response
            else:
                return f(*args, **kwargs)
        return decorated
    return factory

def uses_location_factory(app):
    """Create a decorator that guesses at a location for the client.
    """
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
            return f(*args,  _location=location, **kwargs)
        return decorated
    return factory
