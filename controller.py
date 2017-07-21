from nose.tools import set_trace
import logging
import flask
from flask.ext.babel import lazy_gettext as _
from flask import (
    Response,
    url_for,
)
import requests
import json
import feedparser

from adobe_vendor_id import AdobeVendorIDController

from model import (
    production_session,
    Library,
    get_one_or_create,
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
from util.http import HTTP
from problem_details import *

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
        vendor_id, node_value, delegates = Configuration.vendor_id(self._db)
        if vendor_id:
            self.adobe_vendor_id = AdobeVendorIDController(
                self._db, vendor_id, node_value, delegates
            )
        else:
            self.adobe_vendor_id = None
        
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
        register_url = self.app.url_for("register")
        feed.add_link_to_feed(
            feed.feed, href=register_url, rel="register"
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

    def register(self, do_get=HTTP.get_with_timeout):
        opds_url = flask.request.form.get("url")
        if not opds_url:
            return NO_OPDS_URL

        AUTH_DOCUMENT_REL = "http://opds-spec.org/auth/document"
        SHELF_REL = "http://opds-spec.org/shelf"

        auth_response = None
        links = []
        try:
            response = do_get(opds_url, allowed_response_codes=["2xx", "3xx", 401])
            if response.status_code == 401:
                # The OPDS feed requires authentication, so this response
                # should contain the auth document.
                auth_response = response
            else:
                feed = feedparser.parse(response.content)
                links = feed.get("feed", {}).get("links", [])
        except Exception, e:
            return INVALID_OPDS_FEED

        def find_and_get_url(links, rel, allowed_response_codes=None):
            for link in links:
                if link.get("rel") == rel:
                    url = link.get("href")
                    try:
                        return do_get(url, allowed_response_codes=allowed_response_codes)
                    except Exception, e:
                        pass
            return None

        if not auth_response:
            # The feed didn't require authentication, so we'll need to find
            # the auth document.

            # First, look for a link to the auth document.
            auth_response = find_and_get_url(links, AUTH_DOCUMENT_REL,
                                             allowed_response_codes=["2xx", "3xx"])

        if not auth_response:
            # There was no link to the auth document, but maybe there's a shelf
            # link that requires authentication or links to the document.
            response = find_and_get_url(links, SHELF_REL,
                                        allowed_response_codes=["2xx", "3xx", 401])
            if response:
                if response.status_code == 401:
                    # This response should have the auth document.
                    auth_response = response
                else:
                    # This response didn't require authentication, so maybe it's a feed
                    # that links to the auth document.
                    feed = feedparser.parse(response.content)
                    links = feed.get("feed", {}).get("links", [])
                    auth_response = find_and_get_url(links, AUTH_DOCUMENT_REL,
                                                     allowed_response_codes=["2xx", "3xx"])

        if not auth_response:
            return AUTH_DOCUMENT_NOT_FOUND

        try:
            auth_document = json.loads(auth_response.content)
        except Exception, e:
            return INVALID_AUTH_DOCUMENT

        library, is_new = get_one_or_create(
            self._db, Library,
            opds_url=opds_url
        )

        library.name = auth_document.get("name")
        library.description = auth_document.get("service_description")

        links = auth_document.get("links", {})
        library.web_url = links.get("alternate", {}).get("href", None)
        # TODO: Fetch the logo image and convert to base64 if it's a URL.
        library.logo = links.get("logo", {}).get("href", None)

        if is_new:
            return Response(_("Success"), 201)
        else:
            return Response(_("Success"), 200)
