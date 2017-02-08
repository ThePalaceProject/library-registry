"""Library registry web application."""
import os
import urlparse

from flask import Flask, url_for, redirect, Response, request
from flask.ext.babel import Babel
from flask_sqlalchemy_session import flask_scoped_session

from config import Configuration
from controller import LibraryRegistry
from model import SessionManager
from util.problem_detail import ProblemDetail
from util.app_server import returns_problem_detail

app = Flask(__name__)
debug = Configuration.logging_policy().get("level") == 'DEBUG'
app.config['DEBUG'] = debug
app.debug = debug
babel = Babel(app)

testing = 'TESTING' in os.environ
db_url = Configuration.database_url(testing)
SessionManager.initialize(db_url)
session_factory = SessionManager.sessionmaker(db_url)
_db = flask_scoped_session(session_factory, app)

if os.environ.get('AUTOINITIALIZE') == 'False':
    pass
    # It's the responsibility of the importing code to set app.library_registry
    # appropriately.
else:
    if getattr(app, 'library_registry', None) is None:
        app.library_registry = LibraryRegistry(_db)

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
    return app.library_registry.registry_controller.nearby(
        request.remote_addr
    )

@app.route('/search')
@returns_problem_detail
def search():
    return app.library_registry.registry_controller.search(
        request.remote_addr
    )

@app.route('/heartbeat')
@returns_problem_detail
def hearbeat():
    return app.library_registry.heartbeat.heartbeat()

# Adobe Vendor ID implementation
@app.route('/AdobeAuth/SignIn', methods=['POST'])
@returns_problem_detail
def adobe_vendor_id_signin():
    if app.library_registry.adobe_vendor_id:
        return app.library_registry.adobe_vendor_id.signin_handler()
    else:
        return Response("", 404)
    
@app.route('/AdobeAuth/AccountInfo', methods=['POST'])
@returns_problem_detail
def adobe_vendor_id_accountinfo():
    if app.library_registry.adobe_vendor_id:
        return app.library_registry.adobe_vendor_id.userinfo_handler()
    else:
        return Response("", 404)

@app.route('/AdobeAuth/Status')
@returns_problem_detail
def adobe_vendor_id_status():
    if app.library_registry.adobe_vendor_id:
        return app.library_registry.adobe_vendor_id.status_handler()
    else:
        return Response("", 404)

if __name__ == '__main__':
    debug = True
    url = Configuration.integration_url(
        Configuration.LIBRARY_REGISTRY_INTEGRATION, required=True)
    scheme, netloc, path, parameters, query, fragment = urlparse.urlparse(url)
    if ':' in netloc:
        host, port = netloc.split(':')
        port = int(port)
    else:
        host = netloc
        port = 80

    app.library_registry.log.info("Starting app on %s:%s", host, port)
    app.run(debug=debug, host=host, port=port)
