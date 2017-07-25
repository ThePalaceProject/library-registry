"""Implement logic common to more than one of the Simplified applications."""
from nose.tools import set_trace
from psycopg2 import DatabaseError
import flask
import json
import sys
from lxml import etree
from functools import wraps
from flask import make_response
from flask.ext.babel import lazy_gettext as _
from util.flask_util import problem
from util.problem_detail import ProblemDetail
import traceback
import logging
from opds import OPDSCatalog

from sqlalchemy.orm.session import Session
from sqlalchemy.orm.exc import (
    NoResultFound,
)

def catalog_response(catalog, cache_for=OPDSCatalog.CACHE_TIME):
    content_type = OPDSCatalog.OPDS_TYPE
    return _make_response(catalog, content_type, cache_for)

def _make_response(content, content_type, cache_for):
    if isinstance(content, etree._Element):
        content = etree.tostring(content)
    elif not isinstance(content, basestring):
        content = unicode(content)

    if isinstance(cache_for, int):
        # A CDN should hold on to the cached representation only half
        # as long as the end-user.
        client_cache = cache_for
        cdn_cache = cache_for / 2
        cache_control = "public, no-transform, max-age: %d, s-maxage: %d" % (
            client_cache, cdn_cache)
    else:
        cache_control = "private, no-cache"

    return make_response(content, 200, {"Content-Type": content_type,
                                        "Cache-Control": cache_control})

def returns_problem_detail(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        v = f(*args, **kwargs)
        if isinstance(v, ProblemDetail):
            return v.response
        return v
    return decorated

class ErrorHandler(object):
    def __init__(self, app, debug):
        self.app = app
        self.debug = debug

    def handle(self, exception):
        if hasattr(self.app, 'manager') and hasattr(self.app.manager, '_db'):
            # There is an active database session. Roll it back.
            self.app.manager._db.rollback()
        tb = traceback.format_exc()

        if isinstance(exception, DatabaseError):
            # The database session may have become tainted. For now
            # the simplest thing to do is to kill the entire process
            # and let uwsgi restart it.
            logging.error(
                "Database error: %s Treating as fatal to avoid holding on to a tainted session!",
                exception, exc_info=exception
            )
            shutdown = flask.request.environ.get('werkzeug.server.shutdown')
            if shutdown:
                shutdown()
            else:
                sys.exit()

        # By default, the error will be logged at log level ERROR.
        log_method = logging.error

        # Okay, it's not a database error. Turn it into a useful HTTP error
        # response.
        if hasattr(exception, 'as_problem_detail_document'):
            # This exception can be turned directly into a problem
            # detail document.
            document = exception.as_problem_detail_document(self.debug)
            if not self.debug:
                document.debug_message = None
            else:
                if document.debug_message:
                    document.debug_message += "\n\n" + tb
                else:
                    document.debug_message = tb
            if document.status_code == 502:
                # This is an error in integrating with some upstream
                # service. It's a serious problem, but probably not
                # indicative of a bug in our software. Log it at log level
                # WARN.
                log_method = logging.warn
            response = make_response(document.response)
        else:
            # There's no way to turn this exception into a problem
            # document. This is probably indicative of a bug in our
            # software.
            if self.debug:
                body = tb
            else:
                body = _('An internal error occured')
            response = make_response(unicode(body), 500, {"Content-Type": "text/plain"})

        log_method("Exception in web app: %s", exception, exc_info=exception)
        return response


class HeartbeatController(object):

    def heartbeat(self):
        return make_response("", 200, {"Content-Type": "application/json"})
