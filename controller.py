from nose.tools import set_trace
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
from opds import (
    NavigationFeed,
    Annotator,
)

from util import GeometryUtility
from util.app_server import (
    HeartbeatController,
    feed_response,
)

OPENSEARCH_MEDIA_TYPE = "application/opensearchdescription+xml"

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
        self.registry_controller = LibraryRegistryController(self)
        self.heartbeat = HeartbeatController()

    def url_for(self, view, *args, **kwargs):
        kwargs['_external'] = True
        return url_for(view, *args, **kwargs)


class LibraryRegistryAnnotator(Annotator):

    def __init__(self, app):
        self.app = app
    
    def annotate_feed(self, feed):
        """Add a search link to every feed."""
        search_url = self.app.url_for("search")
        feed.add_link_to_feed(
            feed.feed, href=search_url, rel="search", type=OPENSEARCH_MEDIA_TYPE
        )

    
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
        self.annotator = LibraryRegistryAnnotator(app)
        
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
            self._db, unicode(_("Libraries near you")), this_url, qu,
            annotator=self.annotator
        )
        return feed_response(feed)
        
    def search(self, ip_address=None):
        point = self.point_from_ip(ip_address)
        query = flask.request.args.get('q')
        if query:
            # Run the query and send the results.
            results = Library.search(self._db, point, query)
            this_url = self.app.url_for('search', q=query)
            feed = NavigationFeed(
                self._db, unicode(_('Search results for "%s"')) % query,
                this_url, results,
                annotator=self.annotator
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
            headers['Content-Type'] = OPENSEARCH_MEDIA_TYPE
            headers['Cache-Control'] = "public, no-transform, max-age: %d" % (
                3600 * 24 * 30
            )
            return Response(body, 200, headers)
