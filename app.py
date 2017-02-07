"""Library registry web application."""
from flask import Flask, url_for, redirect, Response

from config import Configuration
from controller import LibraryRegistry
from util.problem_detail import ProblemDetail

app = Flask(__name__)
debug = Configuration.logging_policy().get("level") == 'DEBUG'
app.config['DEBUG'] = debug
app.debug = debug
babel = Babel(app)

if os.environ.get('AUTOINITIALIZE') == 'False':
    pass
    # It's the responsibility of the importing code to set app.library_registry
    # appropriately.
else:
    if getattr(app, 'library_registry', None) is None:
        app.library_registry = LibraryRegistry()

def returns_problem_detail(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        v = f(*args, **kwargs)
        if isinstance(v, ProblemDetail):
            return v.response
        return v
    return decorated

@app.teardown_request
def shutdown_session(exception):
    """Commit or rollback the database session associated with
    the request.
    """
    if (hasattr(app, 'library_registry',)
        and hasattr(app.library_registry, '_db')
        and app.library_registry._db
    ):
        if exception:
            app.library_registry._db.rollback()
        else:
            app.library_registry._db.commit()


@app.route('/')
@returns_problem_detail
def nearby():
    return app.library_registry.nearby()

@app.route('/search')
@returns_problem_detail
def search():
    return app.library_registry.search()

@app.route('/heartbeat')
@returns_problem_detail
def hearbeat():
    return app.heartbeat.heartbeat()
