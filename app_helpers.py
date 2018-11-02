from functools import wraps
from util.problem_detail import ProblemDetail

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
