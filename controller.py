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
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import base64
import os
from PIL import Image
from StringIO import StringIO
from urlparse import urljoin

from adobe_vendor_id import AdobeVendorIDController
from authentication_document import AuthenticationDocument

from model import (
    production_session,
    Library,
    ServiceArea,
    get_one_or_create,
)
from config import (
    Configuration,
    CannotLoadConfiguration,
)
from opds import (
    Annotator,
    OPDSCatalog,
)

from util import GeometryUtility
from util.app_server import (
    HeartbeatController,
    catalog_response,
)
from util.http import HTTP
from problem_details import *

OPENSEARCH_MEDIA_TYPE = "application/opensearchdescription+xml"
OPDS_CATALOG_REGISTRATION_MEDIA_TYPE = "application/opds+json;profile=https://librarysimplified.org/rel/profile/directory"

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
    
    def annotate_catalog(self, catalog):
        """Add links and metadata to every catalog."""
        search_url = self.app.url_for("search")
        catalog.add_link_to_catalog(
            catalog.catalog, href=search_url, rel="search", type=OPENSEARCH_MEDIA_TYPE
        )
        register_url = self.app.url_for("register")
        catalog.add_link_to_catalog(
            catalog.catalog, href=register_url, rel="register", type=OPDS_CATALOG_REGISTRATION_MEDIA_TYPE
        )

        vendor_id, ignore, ignore = Configuration.vendor_id(self.app._db)
        catalog.catalog["metadata"]["adobe_vendor_id"] = vendor_id
    
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
        catalog = OPDSCatalog(
            self._db, unicode(_("Libraries near you")), this_url, qu,
            annotator=self.annotator
        )
        return catalog_response(catalog)
        
    def search(self, ip_address=None):
        point = self.point_from_ip(ip_address)
        query = flask.request.args.get('q')
        if query:
            # Run the query and send the results.
            results = Library.search(self._db, point, query)
            this_url = self.app.url_for('search', q=query)
            catalog = OPDSCatalog(
                self._db, unicode(_('Search results for "%s"')) % query,
                this_url, results,
                annotator=self.annotator
            )
            return catalog_response(catalog)
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
        def get_opds_links(response):
            type = response.headers.get("Content-Type")
            if type == "application/opds+json":
                # This is an OPDS 2 catalog.
                catalog = json.loads(response.content)
                links = []
                for k,v in catalog.get("links", {}).iteritems():
                    links.append(dict(rel=k, href=v.get("href")))
                return links
                
            elif type and type.startswith("application/atom+xml;profile=opds-catalog"):
                # This is an OPDS 1 feed.
                feed = feedparser.parse(response.content)
                return feed.get("feed", {}).get("links", [])
            return []

        links = []
        try:
            response = do_get(opds_url, allowed_response_codes=["2xx", "3xx", 401])
            if response.status_code == 401:
                # The OPDS feed requires authentication, so this response
                # should contain the auth document.
                auth_response = response
            else:
                links = get_opds_links(response)
        except Exception, e:
            return INVALID_OPDS_FEED

        def find_and_get_url(links, rel, allowed_response_codes=None):
            for link in links:
                if link.get("rel") == rel:
                    url = link.get("href")
                    if url:
                        # Expand relative urls.
                        url = urljoin(opds_url, url)
                    try:
                        return do_get(url, allowed_response_codes=allowed_response_codes)
                    except Exception, e:
                        pass
            return None

        if auth_response is None:
            # The feed didn't require authentication, so we'll need to find
            # the auth document.

            # First, look for a link to the auth document.
            auth_response = find_and_get_url(links, AUTH_DOCUMENT_REL,
                                             allowed_response_codes=["2xx", "3xx"])

        if auth_response is None:
            # There was no link to the auth document, but maybe there's a shelf
            # link that requires authentication or links to the document.
            response = find_and_get_url(links, SHELF_REL,
                                        allowed_response_codes=["2xx", "3xx", 401])
            if response is not None:
                if response.status_code == 401:
                    # This response should have the auth document.
                    auth_response = response
                else:
                    # This response didn't require authentication, so maybe it's a feed
                    # that links to the auth document.
                    links = get_opds_links(response)
                    auth_response = find_and_get_url(links, AUTH_DOCUMENT_REL,
                                                     allowed_response_codes=["2xx", "3xx"])

        if auth_response is None:
            return AUTH_DOCUMENT_NOT_FOUND

        try:
            auth_document = AuthenticationDocument.from_string(self._db, auth_response.content)
        except Exception, e:
            return INVALID_AUTH_DOCUMENT

        if not auth_document.id:
            return INVALID_AUTH_DOCUMENT.detailed(_("The OPDS authentication document is missing an id."))
        if not auth_document.title:
            return INVALID_AUTH_DOCUMENT.detailed(_("The OPDS authentication document is missing a title."))
        if auth_document.id != opds_url:
            return INVALID_AUTH_DOCUMENT.detailed(_("The OPDS authentication document's id doesn't match the submitted url."))

        library, is_new = get_one_or_create(
            self._db, Library,
            opds_url=opds_url,
            create_method_kwargs=dict(status=Library.REGISTERED)
        )

        library.name = auth_document.title
        library.description = auth_document.service_description

        if auth_document.website:
            url = auth_document.website.get("href")
            if url:
                url = urljoin(opds_url, url)
            library.web_url = auth_document.website.get("href")
        else:
            library.web_url = None

        if auth_document.logo:
            library.logo = auth_document.logo
        elif auth_document.logo_link:
            url = auth_document.logo_link.get("href")
            if url:
                url = urljoin(opds_url, url)
            logo_response = do_get(url, stream=True)
            try:
                image = Image.open(logo_response.raw)
            except Exception, e:
                return INVALID_AUTH_DOCUMENT.detailed(_("Could not read logo image %(image_url)s", image_url=auth_document.logo_link.get("href")))
            # Convert to PNG.
            buffer = StringIO()
            image.save(buffer, format="PNG")
            b64 = base64.b64encode(buffer.getvalue())
            type = logo_response.headers.get("Content-Type") or auth_document.logo_link.get("type")
            if type:
                library.logo = "data:%s;base64,%s" % (type, b64)
        else:
            library.logo = None

        if auth_document.service_area:
            places, unknown, ambiguous = auth_document.service_area
            if unknown or ambiguous:
                msgs = []
                if unknown:
                    msgs.append(str(_("The following service area was unknown: %(service_area)s.", service_area=json.dumps(unknown))))
                if ambiguous:
                    msgs.append(str(_("The following service area was ambiguous: %(service_area)s.", service_area=json.dumps(ambiguous))))
                return INVALID_AUTH_DOCUMENT.detailed(" ".join(msgs))
            place_ids = []
            for place in places:
                service_area = get_one_or_create(self._db, ServiceArea,
                                                 library_id=library.id,
                                                 place_id=place.id)
                place_ids.append(place.id)
            for service_area in library.service_areas:
                if service_area.place_id not in place_ids:
                    self._db.delete(service_area)
                    
        catalog = OPDSCatalog.library_catalog(library)

        public_key = auth_document.public_key
        if public_key and public_key.get("type") == "RSA":
            public_key = RSA.import_key(public_key.get("value"))
            encryptor = PKCS1_OAEP.new(public_key)

            if not library.short_name:
                # TODO: Generate a short name based on the library's service area.
                library.short_name = os.urandom(3).encode('hex')

            submitted_secret = None
            auth_header = flask.request.headers.get('Authorization')
            if auth_header and isinstance(auth_header, basestring) and "bearer" in auth_header.lower():
                submitted_secret = auth_header.split(' ')[1]
            generate_secret = (library.shared_secret is None) or (submitted_secret == library.shared_secret)
            if generate_secret:
                library.shared_secret = os.urandom(24).encode('hex')

            encrypted_secret = encryptor.encrypt(str(library.shared_secret))

            catalog["metadata"]["short_name"] = library.short_name
            catalog["metadata"]["shared_secret"] = base64.b64encode(encrypted_secret)

        content = json.dumps(catalog)
        headers = dict()
        headers["Content-Type"] = OPDS_CATALOG_REGISTRATION_MEDIA_TYPE

        if is_new:
            return Response(content, 201, headers=headers)
        else:
            return Response(content, 200, headers=headers)
