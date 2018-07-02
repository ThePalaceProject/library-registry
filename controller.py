from collections import defaultdict
from nose.tools import set_trace
import logging
import flask
from flask_babel import lazy_gettext as _
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
    ConfigurationSetting,
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
from util.http import (
    HTTP,
    RequestTimedOut,
)
from util.problem_detail import ProblemDetail
from problem_details import *

OPENSEARCH_MEDIA_TYPE = "application/opensearchdescription+xml"
OPDS_CATALOG_REGISTRATION_MEDIA_TYPE = "application/opds+json;profile=https://librarysimplified.org/rel/profile/directory"

class LibraryRegistry(object):

    def __init__(self, _db=None, testing=False):

        self.log = logging.getLogger("Library registry web app")

        try:
            self.config = Configuration.load()
        except CannotLoadConfiguration, e:
            self.log.error("Could not load configuration file: %s", e)
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
    
    def annotate_catalog(self, catalog, live=True):
        """Add links and metadata to every catalog."""
        if live:
            search_controller = "search"
        else:
            search_controller = "search_qa"
        search_url = self.app.url_for(search_controller)
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
        self.log = self.app.log
  
    def point_from_ip(self, ip_address):
        if not ip_address:
            return None
        return GeometryUtility.point_from_ip(ip_address)

    def stages(self, show_live):
        """Turn a boolean flag into an appropriate list of library stages.

        The list can be passed into one of the Library query methods.
        """
        if show_live:
            return [Library.LIVE]
        else:
            return [Library.APPROVED]
        
    def nearby(self, ip_address, live=True):
        point = self.point_from_ip(ip_address)
        qu = Library.nearby(self._db, point,
                            allowed_stages=self.stages(live))
        qu = qu.limit(5)
        if live:
            nearby_controller = 'nearby'
        else:
            nearby_controller = 'nearby_qa'
        this_url = self.app.url_for(nearby_controller)
        catalog = OPDSCatalog(
            self._db, unicode(_("Libraries near you")), this_url, qu,
            annotator=self.annotator, live=live
        )
        return catalog_response(catalog)
        
    def search(self, ip_address=None, live=True):
        point = self.point_from_ip(ip_address)
        query = flask.request.args.get('q')
        if live:
            search_controller = 'search'
        else:
            search_controller = 'search_qa'
        if query:
            # Run the query and send the results.
            results = Library.search(
                self._db, point, query, allowed_stages=self.stages(live)
            )
                
            this_url = this_url = self.app.url_for(
                search_controller, q=query
            )
            catalog = OPDSCatalog(
                self._db, unicode(_('Search results for "%s"')) % query,
                this_url, results,
                annotator=self.annotator, live=live
            )
            return catalog_response(catalog)
        else:
            # Send the search form.
            body = self.OPENSEARCH_TEMPLATE % dict(
                name=_("Find your library"),
                description=_("Search by ZIP code, city or library name."),
                tags="",
                url_template = self.app.url_for(search_controller) + "?q={searchTerms}"
            )
            headers = {}
            headers['Content-Type'] = OPENSEARCH_MEDIA_TYPE
            headers['Cache-Control'] = "public, no-transform, max-age: %d" % (
                3600 * 24 * 30
            )
            return Response(body, 200, headers)

    @classmethod
    def opds_response_links(cls, response, rel):
        """Find all the links in the given response for the given 
        link relation.
        """
        # Look in the response itself for a Link header.
        links = []
        link = response.links.get(rel)
        if link:
            links.append(link.get('url'))
        media_type = response.headers.get('Content-Type')
        if media_type == OPDSCatalog.OPDS_TYPE:
            # Parse as OPDS 2.
            catalog = json.loads(response.content)
            links = []
            for k,v in catalog.get("links", {}).iteritems():
                if k == rel:
                    links.append(v.get("href"))
        elif media_type == OPDSCatalog.OPDS_1_TYPE:
            # Parse as OPDS 1.
            feed = feedparser.parse(response.content)
            for link in feed.get("feed", {}).get("links", []):
                if link.get('rel') == rel:
                    links.append(link.get('href'))
        elif media_type == AuthenticationDocument.MEDIA_TYPE:
            document = json.loads(response.content)
            if isinstance(document, dict):
                links.append(document.get('id'))
        return [urljoin(response.url, url) for url in links if url]

    @classmethod
    def opds_response_links_to_auth_document(cls, opds_response, auth_url):
        """Verify that the given response links to the given URL as its
        Authentication For OPDS document.
        
        The link might happen in the `Link` header or in the body of
        an OPDS feed.
        """
        links = []
        try:
            links = cls.opds_response_links(
                opds_response, AuthenticationDocument.AUTHENTICATION_DOCUMENT_REL
            )
        except ValueError, e:
            # The response itself is malformed.
            return False
        return auth_url in links

    @property
    def registration_document(self):
        """Serve a document that describes the registration process,
        notably the terms of service for that process.

        The terms of service are included inline as a data: URI,
        to avoid the need to fetch them in a separate request.
        """
        document = dict()
        terms_of_service = ConfigurationSetting.sitewide(
            self._db, Configuration.REGISTRATION_TERMS_OF_SERVICE_TEXT
        ).value
        if terms_of_service:
            terms_of_service = base64.encodestring(terms_of_service)
            terms_of_service_uri = "data:text/html;%s" % terms_of_service
            OPDSCatalog.add_link_to_catalog(
                document, rel="terms-of-service",
                href=terms_of_service_uri
            )
        return document

    def catalog_response(self, document, status=200):
        """Serve an OPDS 2.0 catalog."""
        if not isinstance(document, basestring):
            document = json.dumps(document)
        headers = { "Content-Type": OPDS_CATALOG_REGISTRATION_MEDIA_TYPE }
        return Response(document, status, headers=headers)

    def _required_email_address(self, uri, problem_title):
        """`uri` must be a mailto: URI.

        :return: Either an email address or a customized ProblemDetail.
        """
        problem = None
        on_error = INVALID_CONTACT_URI
        if not uri:
            problem = on_error.detailed("No email address was provided")
        elif not uri.startswith("mailto:"):
            problem = on_error.detailed(
                _("URI must start with 'mailto:' (got: %s)") % uri
            )
        if problem:
            problem.title = problem_title
            return problem
        return uri[7:]

    def _locate_email_address(self, links, problem_title):
        """Find an email address in a list of links.

        :return: Either an email address or a customized ProblemDetail.
        """
        value = None
        for link in links:
            uri = link.get('href')
            value = self._required_email_address(uri, problem_title)
            if isinstance(value, basestring):
                # We found an email address.
                break
        if value is None:
            # There wre no relevant links at all.
            problem = INVALID_CONTACT_URI.detail("No candidate links found.")
            problem.title = problem_title
        return value

    def register(self, do_get=HTTP.get_with_timeout):
        if flask.request.method == 'GET':
            document = self.registration_document
            return self.catalog_response(document)

        auth_url = flask.request.form.get("url")
        if not auth_url:
            return NO_AUTH_URL

        integration_contact_uri = flask.request.form.get("contact")
        integration_contact_email = self._required_email_address(
            integration_contact_uri, "Invalid integration contact address"
        )
        if isinstance(integration_contact_email, ProblemDetail):
            return integration_contact_email

        def _make_request(url, on_404, on_timeout, on_exception, allow_401=False):
            allowed_codes = ["2xx", "3xx", 404]
            if allow_401:
                allowed_codes.append(401)
            try:
                response = do_get(
                    url, allowed_response_codes=allowed_codes,
                    timeout=30
                )
                # We only allowed 404 above so that we could return a more
                # specific problem detail document if it happened.
                if response.status_code == 404:
                    return INTEGRATION_DOCUMENT_NOT_FOUND.detailed(on_404)
                if not allow_401 and response.status_code == 401:
                    self.log.error(
                        "Registration of %s failed: %s is behind authentication gateway",
                        auth_url, url
                    )
                    return ERROR_RETRIEVING_DOCUMENT.detailed(
                        _("%(url)s is behind an authentication gateway",
                          url=url)
                    )
            except RequestTimedOut, e:
                self.log.error(
                    "Registration of %s failed: timeout retrieving %s", 
                    auth_url, url, exc_info=e
                )
                return TIMEOUT.detailed(on_timeout)
            except Exception, e:
                self.log.error(
                    "Registration of %s failed: error retrieving %s",
                    auth_url, url, exc_info=e
                )
                return ERROR_RETRIEVING_DOCUMENT.detailed(on_exception)
            return response

        auth_response = _make_request(
            auth_url, 
            _("No Authentication For OPDS document present at %(url)s", 
              url=auth_url),
            _("Timeout retrieving auth document %(url)s", url=auth_url),
            _("Error retrieving auth document %(url)s", url=auth_url),
        )
        if isinstance(auth_response, ProblemDetail):
            return auth_response
        try:
            auth_document = AuthenticationDocument.from_string(self._db, auth_response.content)
        except Exception, e:
            self.log.error(
                "Registration of %s failed: invalid auth document.",
                auth_url, exc_info=e
            )
            return INVALID_INTEGRATION_DOCUMENT
        failure_detail = None
        if not auth_document.id:
            failure_detail = _("The OPDS authentication document is missing an id.")
        if not auth_document.title:
            failure_detail = _("The OPDS authentication document is missing a title.")
        if auth_document.root:
            opds_url = auth_document.root['href']
        else:
            failure_detail = _("The OPDS authentication document is missing a 'start' link to the root OPDS feed.")
        if auth_document.id != auth_response.url:
            failure_detail = _("The OPDS authentication document's id (%(id)s) doesn't match its url (%(url)s).", id=auth_document.id, url=auth_response.url)
        if failure_detail:
            self.log.error(
                "Registration of %s failed: %s", auth_url, failure_detail
            )
            return INVALID_INTEGRATION_DOCUMENT.detailed(failure_detail)

        # Make sure the authentication document includes a way for
        # patrons to get help or file a copyright complaint.
        links_by_rel = defaultdict(list)
        for l in auth_document.links:
            links_by_rel[l.get('rel')].append(l)
        for rel, problem_title in [
                ('help', "No valid patron help URI"),
                ("http://librarysimplified.org/rel/designated-agent/copyright",
                 "No valid copyright designated agent email address")
        ]:
            links = links_by_rel.get(rel, [])
            if not links:
                problem = INVALID_CONTACT_URI.detailed("")
                problem.title = problem_title
                return problem
            address = self._locate_email_address(links, problem_title)
            if isinstance(address, ProblemDetail):
                return address

        # Cross-check the opds_url to make sure it links back to the
        # authentication document.
        opds_response = _make_request(
            opds_url, 
            _("No OPDS root document present at %(url)s", url=opds_url),
            _("Timeout retrieving OPDS root document at %(url)s", url=opds_url),
            _("Error retrieving OPDS root document at %(url)s", url=opds_url),
            allow_401 = True
        )
        if isinstance(opds_response, ProblemDetail):
            return opds_response

        content_type = opds_response.headers.get('Content-Type')
        failure_detail = None
        if opds_response.status_code == 401:
            # This is only acceptable if the server returned a copy of
            # the Authentication For OPDS document we just got.
            if content_type != AuthenticationDocument.MEDIA_TYPE:
                failure_detail = _("401 response at %(url)s did not yield an Authentication For OPDS document", url=opds_url)
            elif not self.opds_response_links_to_auth_document(
                    opds_response, auth_url
            ):
                failure_detail = _("Authentication For OPDS document guarding %(opds_url)s does not match the one at %(auth_url)s", opds_url=opds_url, auth_url=auth_url)
        elif content_type not in (OPDSCatalog.OPDS_TYPE,
                                OPDSCatalog.OPDS_1_TYPE):
            failure_detail = _("Supposed root document at %(url)s is not an OPDS document", url=opds_url)
        elif not self.opds_response_links_to_auth_document(
                opds_response, auth_url
        ):
            failure_detail = _("OPDS root document at %(opds_url)s does not link back to authentication document %(auth_url)s", opds_url=opds_url, auth_url=auth_url)

        if failure_detail:
            self.log.error(
                "Registration of %s failed: %s", auth_url, failure_detail
            )
            return INVALID_INTEGRATION_DOCUMENT.detailed(failure_detail)

        library, is_new = get_one_or_create(
            self._db, Library,
            opds_url=opds_url,
            create_method_kwargs=dict(stage=Library.REGISTERED)
        )
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
                image_url = auth_document.logo_link.get("href")
                self.log.error(
                    "Registration of %s failed: could not read logo image %s",
                    auth_url, image_url
                )
                return INVALID_INTEGRATION_DOCUMENT.detailed(
                    _("Could not read logo image %(image_url)s", image_url=image_url)
                )
            # Convert to PNG.
            buffer = StringIO()
            image.save(buffer, format="PNG")
            b64 = base64.b64encode(buffer.getvalue())
            type = logo_response.headers.get("Content-Type") or auth_document.logo_link.get("type")
            if type:
                library.logo = "data:%s;base64,%s" % (type, b64)
        else:
            library.logo = None
        problem = auth_document.update_library(library)
        if problem:
            self.log.error(
                "Registration of %s failed: problem during registration: %r",
                auth_url, problem
            )
            return problem
                    
        catalog = OPDSCatalog.library_catalog(library)

        public_key = auth_document.public_key
        if public_key and public_key.get("type") == "RSA":
            public_key = RSA.importKey(public_key.get("value"))
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
        if is_new:
            status_code = 201
        else:
            status_code = 200

        return self.catalog_response(catalog, status_code)
