import logging
import flask
from flask.ext.babel import lazy_gettext as _
from flask import (
    Response,
    url_for,
)

from model import (
    production_session,
    Library,
)
from config import (
    Configuration,
    CannotLoadConfiguration,
)
from opds import NavigationFeed

from util import GeometryUtility
from util.app_server import (
    HeartbeatController,
    feed_response,
)


class LibraryRegistry(object):

    def __init__(self, _db=None, testing=False):

        self.log = logging.getLogger("Content server web app")

        try:
            self.config = Configuration.load()
        except CannotLoadConfiguration, e:
            self.log.error("Could not load configuration file: %s" %e)
            sys.exit()

        if _db is None and not testing:
            _db = production_session()
        self._db = _db

        self.testing = testing

        self.setup_controllers()

    def setup_controllers(self):
        """Set up all the controllers that will be used by the web app."""
        self.library_registry = LibraryRegistryController(self)
        self.heartbeat = HeartbeatController()

    def url_for(self, view, *args, **kwargs):
        kwargs['_external'] = True
        return url_for(view, *args, **kwargs)


class LibraryRegistryController(object):

    OPENSEARCH_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
 <OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
   <ShortName>%(name)s</ShortName>
   <Description>%(description)s</Description>
   <Tags>%(tags)s</Tags>
   <Url type="application/atom+xml;profile=opds-catalog" template="%(url_template)s"/>
 </OpenSearchDescription>"""
    
    def __init__(self, app):
        self.app = app
        self._db = self.app._db

    def point_from_ip(self, ip_address):
        if not ip_address:
            return None
        return GeometryUtility.point_from_ip(ip_address)
        
    def nearby(self, ip_address):
        point = self.point_from_ip(ip_address)
        qu = Library.nearby(self._db, point)
        qu = qu.limit(5)
        this_url = self.app.url_for('nearby')
        feed = NavigationFeed(
            self._db, unicode(_("Find your library")), this_url, qu
        )
        return feed_response(feed)
        
    def search(self, ip_address=None):
        point = self.point_from_ip(ip_address)
        query = flask.request.args.get('q')
        if query:
            # Run the query and send the results.
            results = Library.search(self._db, point, query)
            this_url = self.app.url_for('search')
            feed = NavigationFeed(
                self._db, unicode(_("Search results")), this_url, results
            )
            return feed_response(feed)
        else:
            # Send the search form.
            body = self.OPENSEARCH_TEMPLATE % dict(
                name=_("Find your library"),
                description=_("Search by ZIP code, city or library name."),
                tags="",
                url_template = self.app.url_for('search') + "?q={searchTerms}"
            )
            headers = {}
            headers['Content-Type'] = "application/opensearchdescription+xml"
            headers['Cache-Control'] = "public, no-transform, max-age: %d" % (
                3600 * 24 * 30
            )
            return Response(body, 200, headers)
