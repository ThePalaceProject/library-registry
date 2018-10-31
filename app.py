"""Library registry web application."""
import os
import sys
import urlparse

from flask import Flask, url_for, redirect, Response, request
from flask_babel import Babel
from flask_sqlalchemy_session import flask_scoped_session

from config import Configuration
from controller import LibraryRegistry
from log import LogConfiguration
from model import SessionManager, ConfigurationSetting
from util.problem_detail import ProblemDetail
from util.flask_util import originating_ip
from util.app_server import returns_problem_detail


app = Flask(__name__)
babel = Babel(app)

testing = 'TESTING' in os.environ
db_url = Configuration.database_url(testing)
SessionManager.initialize(db_url)
session_factory = SessionManager.sessionmaker(db_url)
_db = flask_scoped_session(session_factory, app)

log_level = LogConfiguration.initialize(_db, testing=testing)
debug = log_level == 'DEBUG'
app.config['DEBUG'] = debug
app.debug = debug

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
        originating_ip()
    )

@app.route('/qa')
@returns_problem_detail
def nearby_qa():
    return app.library_registry.registry_controller.nearby(
        originating_ip(), live=False
    )

@app.route("/register", methods=["GET","POST"])
@returns_problem_detail
def register():
    return app.library_registry.registry_controller.register()

@app.route('/search')
@returns_problem_detail
def search():
    return app.library_registry.registry_controller.search(
        originating_ip()
    )

@app.route('/qa/search')
@returns_problem_detail
def search_qa():
    return app.library_registry.registry_controller.search(
        originating_ip(), live=False
    )

@app.route('/confirm/<int:resource_id>/<secret>')
@returns_problem_detail
def confirm_resource(resource_id, secret):
    return app.library_registry.validation_controller.confirm(
        resource_id, secret
    )

@app.route('/library/<uuid>')
@returns_problem_detail
def library(uuid):
    return app.library_registry.registry_controller.library(uuid)

@app.route('/coverage')
@returns_problem_detail
def coverage():
    return app.library_registry.coverage_controller.lookup()


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
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = ConfigurationSetting.sitewide(_db, Configuration.BASE_URL).value
    url = url or u'http://localhost:7000/'
    scheme, netloc, path, parameters, query, fragment = urlparse.urlparse(url)
    if ':' in netloc:
        host, port = netloc.split(':')
        port = int(port)
    else:
        host = netloc
        port = 80

    app.library_registry.log.info("Starting app on %s:%s", host, port)
    app.run(debug=debug, host=host, port=port)
