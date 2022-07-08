"""Library registry web application."""
import os
import sys
import urllib.parse

from flask import Flask, Response
from flask_babel import Babel
from flask_sqlalchemy_session import flask_scoped_session

from flask_jwt_extended import JWTManager

from .admin import admin
from .drm import drm
from .library_registration_protocol import libr
from .library_list import libr_list

from library_registry.config import Configuration
from library_registry.controller import LibraryRegistry
from library_registry.decorators import (
    compressible,
    has_library,
    returns_json_or_response_or_problem_detail,
    returns_problem_detail,
    uses_location,
)
from library_registry.log import LogConfiguration
from library_registry.model import SessionManager, ConfigurationSetting


TESTING = 'TESTING' in os.environ
babel = Babel()

db_url = Configuration.database_url(test=TESTING)


def create_app(testing=False, db_session_obj=None):

    app = Flask(__name__)
    app.register_blueprint(drm)
    app.register_blueprint(admin)
    app.register_blueprint(libr)
    app.register_blueprint(libr_list)
    babel.init_app(app)

    jwt = JWTManager(app)
    # app.secret_key = Configuration.SECRET_KEY

    if testing and db_session_obj:
        _db = db_session_obj
    else:
        SessionManager.initialize(db_url)
        session_factory = SessionManager.sessionmaker(db_url)
        _db = flask_scoped_session(session_factory, app)

    log_level = LogConfiguration.initialize(_db, testing=testing)
    debug = log_level == 'DEBUG'
    app.config['DEBUG'] = debug
    app.debug = debug
    app._db = _db

    if not getattr(app, 'library_registry', None):
        app.library_registry = LibraryRegistry(_db)

    @app.before_first_request
    def set_secret_key(_db=None):
        _db = _db or app._db
        app.secret_key = ConfigurationSetting.sitewide_secret(
            _db, Configuration.SECRET_KEY)

    @app.teardown_request
    def shutdown_session(exception):
        """Commit or rollback the database session associated with the request"""
        if (
            hasattr(app, 'library_registry',) and
            hasattr(app.library_registry, '_db') and
            app.library_registry._db
        ):
            if exception:
                app.library_registry._db.rollback()
            else:
                app.library_registry._db.commit()

    return app


app = create_app(testing=TESTING)


if __name__ == '__main__':
    debug = True
    app = create_app()

    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = ConfigurationSetting.sitewide(
            app._db, Configuration.BASE_URL).value

    url = url or 'http://localhost:7000/'
    (scheme, netloc, path, parameters, query,
     fragment) = urllib.parse.urlparse(url)

    if ':' in netloc:
        host, port = netloc.split(':')
        port = int(port)
    else:
        host = netloc
        port = 80

    app.library_registry.log.info("Starting app on %s:%s", host, port)
    app.run(debug=debug, host=host, port=port)
