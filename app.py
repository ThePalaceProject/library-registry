"""Library registry web application."""
import os
import sys
import urllib.parse

from flask import Flask, url_for, redirect, Response, request
from flask_babel import Babel
from flask_sqlalchemy_session import flask_scoped_session

from config import Configuration
from controller import LibraryRegistry
from log import LogConfiguration
from model import SessionManager, ConfigurationSetting

from util.app_server import returns_problem_detail, returns_json_or_response_or_problem_detail
from app_helpers import (
    compressible,
    has_library_factory,
    uses_location_factory,
)


app = Flask(__name__)
babel = Babel(app)

# Create annotators for this app.
has_library = has_library_factory(app)
uses_location = uses_location_factory(app)

testing = 'TESTING' in os.environ
db_url = Configuration.database_url(testing)
SessionManager.initialize(db_url)
session_factory = SessionManager.sessionmaker(db_url)
_db = flask_scoped_session(session_factory, app)

log_level = LogConfiguration.initialize(_db, testing=testing)
debug = log_level == 'DEBUG'
app.config['DEBUG'] = debug
app.debug = debug
app._db = _db

if os.environ.get('AUTOINITIALIZE') == 'False':
    pass
    # It's the responsibility of the importing code to set app.library_registry
    # appropriately.
else:
    if getattr(app, 'library_registry', None) is None:
        app.library_registry = LibraryRegistry(_db)

@app.before_first_request
def set_secret_key(_db=None):
    _db = _db or app._db
    app.secret_key = ConfigurationSetting.sitewide_secret(_db, Configuration.SECRET_KEY)

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
@uses_location
@returns_problem_detail
def nearby(_location):
    return app.library_registry.registry_controller.nearby(_location)

@app.route('/qa')
@uses_location
@returns_problem_detail
def nearby_qa(_location):
    return app.library_registry.registry_controller.nearby(
        _location, live=False
    )

@app.route("/register", methods=["GET","POST"])
@returns_problem_detail
def register():
    return app.library_registry.registry_controller.register()

@app.route('/search')
@uses_location
@returns_problem_detail
def search(_location):
    return app.library_registry.registry_controller.search(_location)

@app.route('/qa/search')
@uses_location
@returns_problem_detail
def search_qa(_location):
    return app.library_registry.registry_controller.search(
        _location, live=False
    )

@app.route('/confirm/<int:resource_id>/<secret>')
@returns_problem_detail
def confirm_resource(resource_id, secret):
    return app.library_registry.validation_controller.confirm(
        resource_id, secret
    )

@app.route('/libraries')
@compressible
@uses_location
@returns_problem_detail
def libraries_opds(_location=None):
    return app.library_registry.registry_controller.libraries_opds(location=_location)

@app.route('/libraries/qa')
@compressible
@uses_location
@returns_problem_detail
def libraries_qa(_location=None):
    return app.library_registry.registry_controller.libraries_opds(location=_location, live=False)

@app.route('/admin/log_in', methods=["POST"])
@returns_problem_detail
def log_in():
    return app.library_registry.registry_controller.log_in()

@app.route('/admin/log_out')
@returns_problem_detail
def log_out():
    return app.library_registry.registry_controller.log_out()

@app.route('/admin/libraries')
@returns_json_or_response_or_problem_detail
def libraries():
    return app.library_registry.registry_controller.libraries()

@app.route('/admin/libraries/qa')
@returns_json_or_response_or_problem_detail
def libraries_qa_admin():
    return app.library_registry.registry_controller.libraries(live=False)

@app.route('/admin/libraries/<uuid>')
@returns_json_or_response_or_problem_detail
def library_details(uuid):
    return app.library_registry.registry_controller.library_details(uuid)

@app.route('/admin/libraries/search_details', methods=["POST"])
@returns_json_or_response_or_problem_detail
def search_details():
    return app.library_registry.registry_controller.search_details()

@app.route('/admin/libraries/email', methods=["POST"])
@returns_json_or_response_or_problem_detail
def validate_email():
    return app.library_registry.registry_controller.validate_email()

@app.route('/admin/libraries/registration', methods=["POST"])
@returns_json_or_response_or_problem_detail
def edit_registration():
    return app.library_registry.registry_controller.edit_registration()

@app.route('/admin/libraries/pls_id', methods=["POST"])
@returns_json_or_response_or_problem_detail
def pls_id():
    return app.library_registry.registry_controller.add_or_edit_pls_id()

@app.route('/library/<uuid>')
@has_library
@returns_json_or_response_or_problem_detail
def library():
    return app.library_registry.registry_controller.library()

@app.route('/library/<uuid>/eligibility')
@has_library
@returns_problem_detail
def library_eligibility():
    return app.library_registry.coverage_controller.eligibility_for_library()

@app.route('/library/<uuid>/focus')
@has_library
@returns_problem_detail
def library_focus():
    return app.library_registry.coverage_controller.focus_for_library()

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


@app.route('/admin/', strict_slashes=False)
def admin_view():
    return app.library_registry.view_controller()


# This path is used only in debug mode to serve frontend assets.
@app.route('/static/<filename>')
def admin_static_file(filename):
    return app.library_registry.static_files.static_file(filename=filename)


if __name__ == '__main__':
    debug = True
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = ConfigurationSetting.sitewide(_db, Configuration.BASE_URL).value
    url = url or 'http://localhost:7000/'
    scheme, netloc, path, parameters, query, fragment = urllib.parse.urlparse(url)
    if ':' in netloc:
        host, port = netloc.split(':')
        port = int(port)
    else:
        host = netloc
        port = 80

    app.library_registry.log.info("Starting app on %s:%s", host, port)
    app.run(debug=debug, host=host, port=port)
