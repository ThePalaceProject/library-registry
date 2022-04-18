import base64
import datetime
import json
import random
import uuid
from smtplib import SMTPException
from urllib.parse import unquote

import pytest       # noqa: F401
import flask
from flask import Response, session
from werkzeug.datastructures import ImmutableMultiDict, MultiDict
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP

from library_registry.authentication_document import AuthenticationDocument
from library_registry.config import Configuration
from library_registry.controller import (
    AdobeVendorIDController,
    BaseController,
    CoverageController,
    LibraryRegistry,
)
from library_registry.library_registration_protocol.controller import (
    LibraryRegistryAnnotator,
    LibraryRegistryController,
    ValidationController,
)
from library_registry.admin.controller import AdminController
from library_registry.emailer import Emailer, EmailTemplate
from library_registry.model import (
    Admin,
    ConfigurationSetting,
    ExternalIntegration,
    Hyperlink,
    Library,
    Place,
    Resource,
    ServiceArea,
    Validation,
)
from library_registry.model_helpers import (get_one, get_one_or_create)
from library_registry.opds import OPDSCatalog
from library_registry.problem_details import (
    ERROR_RETRIEVING_DOCUMENT,
    INTEGRATION_DOCUMENT_NOT_FOUND,
    INTEGRATION_ERROR,
    INVALID_CREDENTIALS,
    INVALID_INTEGRATION_DOCUMENT,
    LIBRARY_NOT_FOUND,
    NO_AUTH_URL,
    TIMEOUT,
    UNABLE_TO_NOTIFY,
)
from library_registry.util import GeometryUtility
from library_registry.util.http import RequestTimedOut
from library_registry.util.problem_detail import ProblemDetail

from .mocks import DummyHTTPClient


class MockLibraryRegistry(LibraryRegistry):
    pass


class MockEmailer(Emailer):

    @classmethod
    def from_sitewide_integration(cls, _db):
        return cls()

    def __init__(self):
        self.sent_out = []

    def send(self, email_type, to_address, **template_args):
        self.sent_out.append((email_type, to_address, template_args))


@pytest.fixture
def mock_registry(db_session):
    library_registry = MockLibraryRegistry(db_session, testing=True, emailer_class=MockEmailer)
    yield library_registry


@pytest.fixture
def mock_registry_controller(mock_registry):
    registry_controller = LibraryRegistryController(mock_registry, emailer_class=MockEmailer)
    yield registry_controller


@pytest.fixture
def mock_admin_controller(mock_registry):
    admin_controller = AdminController(mock_registry, emailer_class=MockEmailer)
    yield admin_controller


@pytest.fixture
def adobe_integration(db_session, create_test_external_integration):
    integration = create_test_external_integration(
        db_session, protocol=ExternalIntegration.ADOBE_VENDOR_ID, goal=ExternalIntegration.DRM_GOAL,
    )
    integration.setting(Configuration.ADOBE_VENDOR_ID).value = "VENDORID"
    db_session.commit()
    yield integration
    db_session.delete(integration)
    db_session.commit()


class TestLibraryRegistryAnnotator:
    def test_annotate_catalog(self, app, db_session, adobe_integration):
        annotator = LibraryRegistryAnnotator(app.library_registry)

        with app.test_request_context("/"):
            catalog = OPDSCatalog(db_session, "Test Catalog", "http://catalog", [])
            annotator.annotate_catalog(catalog)

            # The catalog should have three new links: search, register, and a templated link
            # for a library's OPDS entry, in addition to self. It should also have the adobe
            # vendor id in the catalog's metadata.

            links = catalog.catalog.get("links")
            assert len(links) == 4
            [opds_link, register_link, search_link, self_link] = sorted(links, key=lambda x: x.get("rel"))

            assert opds_link.get("href") == 'http://localhost/library/{uuid}'
            assert opds_link.get("rel") == 'http://librarysimplified.org/rel/registry/library'
            assert opds_link.get("type") == 'application/opds+json'
            assert opds_link.get("templated") is True

            assert search_link.get("href") == 'http://localhost/search'
            assert search_link.get("rel") == "search"
            assert search_link.get("type") == 'application/opensearchdescription+xml'

            assert register_link.get("href") == 'http://localhost/register'
            assert register_link.get('rel') == 'register'
            assert register_link.get('type') == (
                'application/opds+json;profile=https://librarysimplified.org/rel/profile/directory'
            )

            assert catalog.catalog.get("metadata").get('adobe_vendor_id') == "VENDORID"


class TestBaseController:
    def test_library_for_request(self, app, db_session, create_test_library, mock_registry):
        # Test the code that looks up a library by its UUID and sets it as flask.request.library.
        controller = BaseController(mock_registry)
        f = controller.library_for_request
        library = create_test_library(db_session)

        with app.test_request_context("/"):
            assert f(None) == LIBRARY_NOT_FOUND
            assert f("no such uuid") == LIBRARY_NOT_FOUND

            assert f(library.internal_urn) == library
            assert flask.request.library == library

            flask.request.library = None
            assert f(library.internal_urn[len("urn:uuid:"):]) == library
            assert flask.request.library == library


class TestLibraryRegistry:
    
    def test_instantiated_controllers_with_adobe(self, db_session, adobe_integration):
        registry_with_adobe = MockLibraryRegistry(db_session, testing=True, emailer_class=MockEmailer)
        assert isinstance(registry_with_adobe.adobe_vendor_id, AdobeVendorIDController)

    def test_instantiated_controllers_without_adobe(self, mock_registry):
        # Verify that the controllers were instantiated and attached to the LibraryRegistry object.
        assert isinstance(mock_registry.registry_controller, LibraryRegistryController)
        assert isinstance(mock_registry.validation_controller, ValidationController)

        # No Adobe Vendor ID was set up.
        assert mock_registry.adobe_vendor_id is None


@pytest.fixture
def registration_form():
    registration_form = ImmutableMultiDict([
        ("url", "http://circmanager.org/authentication.opds"),
        ("contact", "mailto:integrationproblems@library.org"),
    ])
    yield registration_form


@pytest.fixture
def http_client():
    return DummyHTTPClient()


@pytest.fixture
def manhattan():
    return GeometryUtility.point_from_ip("65.88.88.124")


@pytest.fixture
def oakland():
    return GeometryUtility.point_from_string("37.8,-122.2")


@pytest.fixture
def generate_auth_document(kansas_state):
    def _generate_auth_document(key=None):
        auth_document = {
            "id": "http://circmanager.org/authentication.opds",
            "title": "A Library",
            "service_description": "Description",
            "authentication": [
                {
                    "type": "https://librarysimplified.org/rel/auth/anonymous"
                }
            ],
            "links": [
                {"rel": "alternate", "href": "http://circmanager.org", "type": "text/html"},
                {"rel": "logo", "href": "data:image/png;imagedata"},
                {"rel": "register", "href": "http://circmanager.org/new-account"},
                {"rel": "start", "href": "http://circmanager.org/feed/",
                    "type": "application/atom+xml;profile=opds-catalog"},
                {"rel": "help", "href": "http://help.library.org/"},
                {"rel": "help", "href": "mailto:help@library.org"},
                {"rel": "http://librarysimplified.org/rel/designated-agent/copyright",
                    "href": "mailto:dmca@library.org"},
            ],
            "service_area": {"US": "Kansas"},
            "collection_size": 100,
        }

        if key:
            auth_document['public_key'] = {
                "type": "RSA",
                "value": key.publickey().exportKey().decode("utf8")
            }
        return auth_document

    return _generate_auth_document


@pytest.fixture(scope="function", autouse=True)
def teardown(db_session, capsys):
    yield 1
    for resource in db_session.query(Resource).all():
        db_session.delete(resource)

    for hyperlink in db_session.query(Hyperlink).all():
        db_session.delete(hyperlink)

    for admin in db_session.query(Admin).all():
        db_session.delete(admin)

    db_session.commit()


class TestLibraryRegistryController:

    def test_libraries_opds(
        self, db_session, create_test_library, mock_registry_controller, mock_admin_controller, app,
        nypl, connecticut_state_library, kansas_state_library
    ):
        library = create_test_library(db_session, library_name="Test Cancelled Library",
                                      short_name="test_cancelled_lib",
                                      library_stage=Library.CANCELLED_STAGE,
                                      registry_stage=Library.TESTING_STAGE)
        response = mock_admin_controller.libraries()
        libraries = response.get("libraries")

        # There are currently four libraries, but only the three in production are shown.
        assert len(libraries) == 3

        with app.test_request_context("/libraries"):
            response = mock_registry_controller.libraries_opds()

            assert response.status == "200 OK"
            assert response.headers['Content-Type'] == OPDSCatalog.OPDS_TYPE

            catalog = json.loads(response.data)

            # In the OPDS response, instead of getting four libraries like
            # libraries_qa() returns, we should only get three back because
            # the last library has a stage that is cancelled.
            assert len(catalog['catalogs']) == 3

            [ct_catalog, ks_catalog, nypl_catalog] = catalog['catalogs']
            assert ct_catalog['metadata']['title'] == "Connecticut State Library"
            assert ct_catalog['metadata']['id'] == connecticut_state_library.internal_urn

            assert ks_catalog['metadata']['title'] == "Kansas State Library"
            assert ks_catalog['metadata']['id'] == kansas_state_library.internal_urn

            assert nypl_catalog['metadata']['title'] == "NYPL"
            assert nypl_catalog['metadata']['id'] == nypl.internal_urn

            [library_link, register_link, search_link, self_link] = sorted(
                catalog['links'], key=lambda x: x['rel']
            )
            url_for = app.library_registry.url_for

            assert self_link['href'] == url_for("libr.libraries_opds")
            assert self_link['rel'] == "self"
            assert self_link['type'] == OPDSCatalog.OPDS_TYPE

            # Try again with a location in Kansas.
            #
            # See test_app_server.py to verify that @uses_location
            # converts normal-looking latitude/longitude into this
            # format.
            with app.test_request_context("/libraries"):
                response = mock_registry_controller.libraries_opds(location="SRID=4326;POINT(-98 39)")

            catalog = json.loads(response.data)
            titles = [x['metadata']['title'] for x in catalog['catalogs']]

            # The nearby library is promoted to the top of the list.
            # The other libraries are still in alphabetical order.
            assert titles == ['Kansas State Library', 'Connecticut State Library', 'NYPL']

    def test_instantiate_without_emailer(self, mock_registry):
        """If there is no emailer configured, the controller will still start up."""
        controller = LibraryRegistryController(mock_registry)
        assert controller.emailer is None

    def test_nearby(
        self, app, mock_registry_controller, nypl, connecticut_state_library, manhattan, adobe_integration
    ):
        with app.test_request_context("/"):
            response = mock_registry_controller.nearby(manhattan, live=True)
            assert isinstance(response, Response)
            assert "200 OK" == response.status
            assert response.headers['Content-Type'] == OPDSCatalog.OPDS_TYPE
            catalog = json.loads(response.data)

            # The catalog can be cached for a while, since the list of libraries
            # doesn't change very quickly.
            assert response.headers['Cache-Control'] == "public, no-transform, max-age: 43200, s-maxage: 21600"

            # We found both libraries within a 150-kilometer radius of the
            # starting point.
            nypl_catalog, ct_catalog = catalog['catalogs']
            assert nypl_catalog['metadata']['title'] == "NYPL"
            assert nypl_catalog['metadata']['distance'] == "0 km."
            assert ct_catalog['metadata']['title'] == "Connecticut State Library"
            assert ct_catalog['metadata']['distance'] == "29 km."

            # If that's not good enough, there's a link to the search
            # controller, so you can do a search.
            [library_link, register_link, search_link, self_link] = sorted(
                catalog['links'], key=lambda x: x['rel']
            )
            url_for = app.library_registry.url_for

            assert self_link['href'] == url_for("libr.nearby")
            assert self_link['rel'] == "self"
            assert self_link['type'] == OPDSCatalog.OPDS_TYPE

            assert search_link['href'] == url_for("libr.search")
            assert search_link['rel'] == "search"
            assert search_link['type'] == "application/opensearchdescription+xml"

            assert register_link["href"] == url_for("libr.register")
            assert register_link["rel"] == "register"
            assert register_link["type"] == (
                "application/opds+json;profile=https://librarysimplified.org/rel/profile/directory"
            )

            assert library_link["href"] == unquote(url_for("libr.library", uuid="{uuid}"))
            assert library_link["rel"] == "http://librarysimplified.org/rel/registry/library"
            assert library_link["type"] == "application/opds+json"
            assert library_link.get("templated") is True

            assert catalog["metadata"]["adobe_vendor_id"] == "VENDORID"

    def test_nearby_qa(self, db_session, app, mock_registry_controller, manhattan, nypl, connecticut_state_library):
        # The libraries we used in the previous test are in production.
        # If we move them from production to TESTING, we won't find anything.
        for library in db_session.query(Library):
            library.registry_stage = Library.TESTING_STAGE

        with app.test_request_context("/"):
            response = mock_registry_controller.nearby(manhattan, live=True)
            catalogs = json.loads(response.data)
            assert catalogs['catalogs'] == []

        # However, they will show up in the QA feed.
        with app.test_request_context("/"):
            response = mock_registry_controller.nearby(manhattan, live=False)
            catalogs = json.loads(response.data)
            assert len(catalogs['catalogs']) == 2
            [catalog] = [
                x for x in catalogs['catalogs']
                if x['metadata']['id'] == nypl.internal_urn
            ]
            assert catalog['metadata']['title'] == "NYPL"

            # Some of the links are the same as in the production feed;
            # others are different.
            url_for = app.library_registry.url_for
            [library_link, register_link, search_link, self_link] = sorted(
                catalogs['links'], key=lambda x: x['rel']
            )

            # The 'register' link is the same as in the main feed.
            assert register_link["href"] == url_for("libr.register")
            assert register_link["rel"] == "register"

            # So is the 'library' templated link.
            assert library_link["href"] == unquote(url_for("libr.library", uuid="{uuid}"))
            assert library_link["rel"] == "http://librarysimplified.org/rel/registry/library"

            # This is a QA feed, and the 'search' and 'self' links
            # will give results from the QA feed.
            assert self_link['href'] == url_for("libr.nearby_qa")
            assert self_link['rel'] == "self"

            assert search_link['href'] == url_for("libr.search_qa")
            assert search_link['rel'] == "search"

    def test_nearby_no_location(self, app, mock_registry_controller):
        with app.test_request_context("/"):
            response = mock_registry_controller.nearby(None)
            assert isinstance(response, Response)
            assert response.status == "200 OK"
            assert response.headers['Content-Type'] == OPDSCatalog.OPDS_TYPE
            catalogs = json.loads(response.data)

            # We found no nearby libraries, because we had no location to
            # start with.
            assert catalogs['catalogs'] == []

    def test_nearby_no_libraries(self, app, mock_registry_controller, oakland):
        with app.test_request_context("/"):
            response = mock_registry_controller.nearby(oakland)
            assert isinstance(response, Response)
            assert response.status == "200 OK"
            assert response.headers['Content-Type'] == OPDSCatalog.OPDS_TYPE
            catalog = json.loads(response.data)

            # We found no nearby libraries, because we were across the
            # country from the only ones in the registry.
            assert catalog['catalogs'] == []

    def test_search_form(self, app, mock_registry_controller, mock_registry):
        with app.test_request_context("/"):
            response = mock_registry_controller.search(None)
            assert response.status == "200 OK"
            assert response.headers['Content-Type'] == "application/opensearchdescription+xml"

            # The search form can be cached more or less indefinitely.
            assert response.headers['Cache-Control'] == "public, no-transform, max-age: 2592000"

            # The search form points the client to the search controller.
            expect_url = mock_registry.url_for("libr.search")
            expect_url_tag = (
                '<Url type="application/atom+xml;profile=opds-catalog" template="%s?q={searchTerms}"/>' % expect_url
            )
            assert expect_url_tag in response.data.decode("utf8")

    def test_qa_search_form(self, app, mock_registry_controller, mock_registry):
        """The QA search form links to the QA search controller."""
        with app.test_request_context("/"):
            response = mock_registry_controller.search(None, live=False)
            assert response.status == "200 OK"

            expect_url = mock_registry.url_for("libr.search_qa")
            expect_url_tag = (
                '<Url type="application/atom+xml;profile=opds-catalog" template="%s?q={searchTerms}"/>' % expect_url
            )
            assert expect_url_tag in response.data.decode("utf8")

    def test_search(self, app, mock_registry_controller, nypl, kansas_state_library, manhattan, adobe_integration):
        with app.test_request_context("/?q=manhattan"):
            response = mock_registry_controller.search(manhattan)
            assert response.status == "200 OK"
            assert response.headers['Content-Type'] == OPDSCatalog.OPDS_TYPE
            catalog = json.loads(response.data)
            # We found the two matching results.
            [nypl_catalog, ks_catalog] = catalog['catalogs']
            assert nypl_catalog['metadata']['title'] == "NYPL"
            assert nypl_catalog['metadata']['distance'] == "0 km."

            assert ks_catalog['metadata']['title'] == "Kansas State Library"
            assert ks_catalog['metadata']['distance'] == "1928 km."

            [library_link, register_link, search_link, self_link] = sorted(
                catalog['links'], key=lambda x: x['rel']
            )
            url_for = app.library_registry.url_for

            # The search results have a self link and a link back to the search form.
            assert self_link['href'] == url_for("libr.search", q="manhattan")
            assert self_link['rel'] == "self"
            assert self_link['type'] == OPDSCatalog.OPDS_TYPE

            assert search_link['href'] == url_for("libr.search")
            assert search_link['rel'] == "search"
            assert search_link['type'] == "application/opensearchdescription+xml"

            assert register_link["href"] == url_for("libr.register")
            assert register_link["rel"] == "register"
            assert register_link["type"] == (
                "application/opds+json;profile=https://librarysimplified.org/rel/profile/directory"
            )

            assert library_link["href"] == unquote(url_for("libr.library", uuid="{uuid}"))
            assert library_link["rel"] == "http://librarysimplified.org/rel/registry/library"
            assert library_link["type"] == "application/opds+json"
            assert library_link.get("templated") is True

            assert catalog["metadata"]["adobe_vendor_id"] == "VENDORID"

    def test_search_qa(self, db_session, app, mock_registry_controller, manhattan, nypl, kansas_state_library):
        # As we saw in the previous test, this search picks up two
        # libraries when we run it looking for production libraries. If
        # all of the libraries are cancelled, we don't find anything.
        for lib in db_session.query(Library):
            assert lib.registry_stage == Library.PRODUCTION_STAGE

        for lib in db_session.query(Library):
            lib.registry_stage = Library.CANCELLED_STAGE

        with app.test_request_context("/?q=manhattan"):
            response = mock_registry_controller.search(manhattan, live=True)
            catalog = json.loads(response.data)
            assert catalog['catalogs'] == []

        # If we move one of the libraries back into the PRODUCTION
        # stage, we find it.
        kansas_state_library.registry_stage = Library.PRODUCTION_STAGE
        with app.test_request_context("/?q=manhattan"):
            response = mock_registry_controller.search(manhattan, live=True)
            catalog = json.loads(response.data)
            [catalog] = catalog['catalogs']
            assert catalog['metadata']['title'] == 'Kansas State Library'

    def test_library(self, nypl, app, mock_registry_controller):
        with app.test_request_context("/"):
            flask.request.library = nypl
            response = mock_registry_controller.library()

        [catalog_entry] = json.loads(response.data).get("catalogs")
        assert catalog_entry.get("metadata").get("title") == nypl.name
        assert catalog_entry.get("metadata").get("id") == nypl.internal_urn

    def queue_opds_success(
        self, http_client_obj, auth_url="http://circmanager.org/authentication.opds", media_type=None
    ):
        """
        The next HTTP request made by the registry will appear to retrieve a functional OPDS feed
        that links to `auth_url` as its Authentication For OPDS document.
        """
        media_type = media_type or OPDSCatalog.OPDS_1_TYPE
        http_client_obj.queue_response(
            200,
            media_type,
            links={
                AuthenticationDocument.AUTHENTICATION_DOCUMENT_REL: {
                    'url': auth_url, 'rel': AuthenticationDocument.AUTHENTICATION_DOCUMENT_REL
                }
            }
        )

    def test_register_get(self, db_session, app, mock_registry_controller):
        # When there is no terms-of-service document, you can get a
        # document describing the authentication process but it's
        # empty.
        with app.test_request_context("/", method="GET"):
            response = mock_registry_controller.register()
            assert response.status_code == 200
            assert json.loads(response.data) == {}

        # Set a terms-of-service link.
        tos = "http://terms.com/service.html"
        ConfigurationSetting.sitewide(
            db_session, Configuration.REGISTRATION_TERMS_OF_SERVICE_URL
        ).value = tos

        # And a terms-of-service HTML snippet.
        html = 'Terms of service are <a href="http://terms.com/service.html">over here</a>.'
        ConfigurationSetting.sitewide(
            db_session, Configuration.REGISTRATION_TERMS_OF_SERVICE_HTML
        ).value = html

        # Now the document contains two links, both with the
        # 'terms-of-service' rel. One links to the terms of service
        # document, the other is a data: URI containing a snippet of
        # HTML.
        with app.test_request_context("/", method="GET"):
            response = mock_registry_controller.register()
            assert response.status_code == 200
            data = json.loads(response.data)

            # Both links have the same rel and type.
            for link in data['links']:
                assert link["rel"] == "terms-of-service"
                assert link["type"] == "text/html"

            # Verifying the http: link is simple.
            [http_link, data_link] = data['links']
            assert http_link['href'] == tos

            # To verify the data: link we must first separate it from its
            # header and decode it.
            header, encoded = data_link['href'].split(",", 1)
            assert header == "data:text/html;base64"

            decoded = base64.b64decode(encoded)
            assert decoded.decode("utf8") == html

    def test_register_fails_when_no_auth_document_url_provided(
        self, app, mock_registry_controller, http_client
    ):
        """Without the URL to an Authentication For OPDS document,
        the registration process can't begin.
        """
        with app.test_request_context("/", method="POST"):
            response = mock_registry_controller.register(do_get=http_client.do_get)

            assert response == NO_AUTH_URL

    def test_register_fails_when_auth_document_url_times_out(
        self, app, registration_form, mock_registry_controller, http_client
    ):
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            http_client.queue_response(RequestTimedOut("http://url", "sorry"))
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == TIMEOUT.uri
            assert response.detail == 'Timeout retrieving auth document http://circmanager.org/authentication.opds'

    def test_register_fails_on_non_200_code(
        self, app, mock_registry_controller, registration_form, http_client
    ):
        """
        If the URL provided results in a status code other than
        200, the registration process can't begin.
        """
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form

            # This server isn't working.
            http_client.queue_response(500)
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == ERROR_RETRIEVING_DOCUMENT.uri
            assert response.detail == "Error retrieving auth document http://circmanager.org/authentication.opds"

            # This server incorrectly requires authentication to
            # access the authentication document.
            http_client.queue_response(401)
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == ERROR_RETRIEVING_DOCUMENT.uri
            assert response.detail == "Error retrieving auth document http://circmanager.org/authentication.opds"

            # This server doesn't have an authentication document
            # at the specified URL.
            http_client.queue_response(404)
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == INTEGRATION_DOCUMENT_NOT_FOUND.uri
            assert response.detail == (
                'No Authentication For OPDS document present at http://circmanager.org/authentication.opds'
            )

    def test_register_fails_on_non_authentication_document(
        self, app, mock_registry_controller, registration_form, http_client
    ):
        # The request succeeds but returns something other than
        # an authentication document.
        http_client.queue_response(200, content="I am not an Authentication For OPDS document.")
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response == INVALID_INTEGRATION_DOCUMENT

    def test_register_fails_on_non_matching_id(
        self, app, mock_registry_controller, http_client, generate_auth_document
    ):
        # The request returns an authentication document but its `id`
        # doesn't match the final URL it was retrieved from.
        auth_document = generate_auth_document()
        http_client.queue_response(
            200, content=json.dumps(auth_document),
            url="http://a-different-url/"
        )
        with app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict([
                ("url", "http://a-different-url/"),
                ("contact", "mailto:me@library.org"),
            ])
            response = mock_registry_controller.register(do_get=http_client.do_get)

            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert response.detail == (
                "The OPDS authentication document's id (http://circmanager.org/authentication.opds) "
                "doesn't match its url (http://a-different-url/)."
            )

    def test_register_fails_on_missing_title(
        self, app, registration_form, mock_registry_controller, http_client, generate_auth_document
    ):
        # The request returns an authentication document but it's missing
        # a title.
        auth_document = generate_auth_document()
        del auth_document['title']
        http_client.queue_response(200, content=json.dumps(auth_document), url=auth_document['id'])
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert response.detail == "The OPDS authentication document is missing a title."

    def test_register_fails_on_no_start_link(
        self, app, mock_registry_controller, registration_form, http_client, generate_auth_document
    ):
        # The request returns an authentication document but it's missing
        # a link to an OPDS feed.
        auth_document = generate_auth_document()
        for link in list(auth_document['links']):
            if link['rel'] == 'start':
                auth_document['links'].remove(link)
        http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document['id']
        )
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert response.detail == (
                "The OPDS authentication document is missing a 'start' link to the root OPDS feed."
            )

    def test_register_fails_on_start_link_not_found(
        self, app, http_client, mock_registry_controller, registration_form, generate_auth_document
    ):
        # The request returns an authentication document but an attempt
        # to retrieve the corresponding OPDS feed yields a 404.
        auth_document = generate_auth_document()
        http_client.queue_response(
            200, content=json.dumps(auth_document),
            url=auth_document['id']
        )
        http_client.queue_response(404)
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == INTEGRATION_DOCUMENT_NOT_FOUND.uri
            assert response.detail == "No OPDS root document present at http://circmanager.org/feed/"

    def test_register_fails_on_start_link_timeout(
        self, app, http_client, mock_registry_controller, registration_form, generate_auth_document
    ):
        # The request returns an authentication document but an attempt
        # to retrieve the corresponding OPDS feed times out.
        auth_document = generate_auth_document()
        http_client.queue_response(200, content=json.dumps(auth_document), url=auth_document['id'])
        http_client.queue_response(RequestTimedOut("http://url", "sorry"))
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == TIMEOUT.uri
            assert response.detail == "Timeout retrieving OPDS root document at http://circmanager.org/feed/"

    def test_register_fails_on_start_link_error(
        self, http_client, app, registration_form, mock_registry_controller, generate_auth_document
    ):
        # The request returns an authentication document but an attempt
        # to retrieve the corresponding OPDS feed gives a server-side error.
        auth_document = generate_auth_document()
        http_client.queue_response(200, content=json.dumps(auth_document), url=auth_document['id'])
        http_client.queue_response(500)
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == ERROR_RETRIEVING_DOCUMENT.uri
            assert response.detail == "Error retrieving OPDS root document at http://circmanager.org/feed/"

    def test_register_fails_on_start_link_not_opds_feed(
        self, http_client, app, mock_registry_controller, registration_form, generate_auth_document
    ):
        """The request returns an authentication document but an attempt
        to retrieve the corresponding OPDS feed gives a server-side error.
        """
        auth_document = generate_auth_document()
        http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document['id']
        )

        # The start link returns a 200 response code but the wrong
        # Content-Type.
        http_client.queue_response(200, "text/html")
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert response.detail == (
                "Supposed root document at http://circmanager.org/feed/ is not an OPDS document"
            )

    def test_register_fails_if_start_link_does_not_link_back_to_auth_document(
        self, http_client, app, registration_form, mock_registry_controller, generate_auth_document
    ):
        auth_document = generate_auth_document()
        http_client.queue_response(200, content=json.dumps(auth_document), url=auth_document['id'])

        # The start link returns a 200 response code and the right
        # Content-Type, but there is no Link header and the body is no
        # help.
        http_client.queue_response(200, OPDSCatalog.OPDS_TYPE, content='{}')
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert response.detail == (
                "OPDS root document at http://circmanager.org/feed/ does not link back to "
                "authentication document http://circmanager.org/authentication.opds"
            )

    def test_register_fails_on_broken_logo_link(
        self, http_client, app, mock_registry_controller, registration_form, generate_auth_document
    ):
        """
        The request returns a valid authentication document that links to a broken logo image.
        """
        auth_document = generate_auth_document()
        for link in auth_document['links']:
            if link['rel'] == 'logo':
                link['href'] = "http://example.com/broken-logo.png"
                break
        # Auth document request succeeds.
        http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document['id']
        )

        # OPDS feed request succeeds.
        self.queue_opds_success(http_client)

        # Image request fails.
        http_client.queue_response(500)

        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert response.detail == "Could not read logo image http://example.com/broken-logo.png"

    def test_register_fails_on_unknown_service_area(
        self, app, registration_form, http_client, mock_registry_controller, generate_auth_document
    ):
        """
        The auth document is valid but the registry doesn't recognize the library's service area.
        """
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            auth_document = generate_auth_document()
            auth_document['service_area'] = {"US": ["Somewhere"]}
            http_client.queue_response(200, content=json.dumps(auth_document), url=auth_document['id'])
            self.queue_opds_success(http_client)
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert response.detail == "The following service area was unknown: {\"US\": [\"Somewhere\"]}."

    def test_register_fails_on_ambiguous_service_area(
        self, crude_us, new_york_city, manhattan_ks, app, registration_form, http_client,
        mock_registry_controller, generate_auth_document
    ):
        # Create a situation (which shouldn't exist in real life)
        # where there are two places with the same name and the same
        # .parent.
        new_york_city.parent = crude_us
        manhattan_ks.parent = crude_us

        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            auth_document = generate_auth_document()
            auth_document['service_area'] = {"US": ["Manhattan"]}
            http_client.queue_response(
                200, content=json.dumps(auth_document),
                url=auth_document['id']
            )
            self.queue_opds_success(http_client)
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert response.detail == "The following service area was ambiguous: {\"US\": [\"Manhattan\"]}."

    def test_register_fails_on_401_with_no_authentication_document(
        self, app, registration_form, http_client, mock_registry_controller, generate_auth_document
    ):
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            auth_document = generate_auth_document()
            http_client.queue_response(
                200, content=json.dumps(auth_document), url=auth_document['id']
            )
            http_client.queue_response(401)
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert response.detail == (
                "401 response at http://circmanager.org/feed/ did not yield an Authentication For OPDS document"
            )

    def test_register_fails_on_401_if_authentication_document_ids_do_not_match(
        self, app, registration_form, http_client, mock_registry_controller, generate_auth_document
    ):
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            auth_document = generate_auth_document()
            http_client.queue_response(
                200, content=json.dumps(auth_document),
                url=auth_document['id']
            )
            auth_document['id'] = "http://some-other-id/"
            http_client.queue_response(
                401, AuthenticationDocument.MEDIA_TYPE,
                content=json.dumps(auth_document),
                url=auth_document['id']
            )

            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert response.detail == (
                "Authentication For OPDS document guarding http://circmanager.org/feed/ does not match "
                "the one at http://circmanager.org/authentication.opds"
            )

    def test_register_succeeds_on_401_if_authentication_document_ids_match(
        self, app, registration_form, http_client, mock_registry_controller,
        generate_auth_document, db_session
    ):
        with app.test_request_context("/", method="POST"):
            flask.request.form = registration_form
            auth_document = generate_auth_document()
            http_client.queue_response(200, content=json.dumps(auth_document), url=auth_document['id'])
            http_client.queue_response(401, AuthenticationDocument.MEDIA_TYPE,
                                       content=json.dumps(auth_document), url=auth_document['id'])

            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.status_code == 201

        [test_library_to_destroy] = db_session.query(Library).filter(Library.name == 'A Library').all()

    # NOTE: This is commented out until we can say that registration
    # requires providing a contact email and expect every new library
    # to be on a circulation manager that can meet this requirement.
    #
    # def test_register_fails_on_no_contact_email(self):
    #     with app.test_request_context("/", method="POST"):
    #         flask.request.form = ImmutableMultiDict([
    #             ("url", "http://circmanager.org/authentication.opds"),
    #         ])
    #         response = mock_registry_controller.register(do_get=http_client.do_get)
    #         assert response.title == "Invalid or missing configuration contact email address"

    #         flask.request.form = ImmutableMultiDict([
    #             ("url", "http://circmanager.org/authentication.opds"),
    #             ("contact", "http://contact-us/")
    #         ])
    #         response = mock_registry_controller.register(do_get=http_client.do_get)
    #         assert response.title == "Invalid or missing configuration contact email address"

    def test_register_fails_on_missing_email_in_authentication_document(
        self, http_client, app, mock_registry_controller, registration_form, generate_auth_document
    ):
        for (rel, error) in (
                ("http://librarysimplified.org/rel/designated-agent/copyright",
                 "Invalid or missing copyright designated agent email address"),
                ("help", "Invalid or missing patron support email address")
        ):
            # Start with a valid document.
            auth_document = generate_auth_document()

            # Remove the crucial link.
            auth_document['links'] = [x for x in auth_document['links']
                                      if x['rel'] != rel or not x['href'].startswith("mailto:")]

            def _request_fails():
                http_client.queue_response(
                    200, content=json.dumps(auth_document),
                    url=auth_document['id']
                )
                with app.test_request_context("/", method="POST"):
                    flask.request.form = registration_form
                    response = mock_registry_controller.register(do_get=http_client.do_get)
                    assert response.title == error

            _request_fails()

            # Now add the link back but as an http: link.
            auth_document['links'].append(dict(rel=rel, href="http://not-an-email/"))
            _request_fails()

    def test_registration_fails_if_email_server_fails(
        self, mock_registry_controller, http_client, app, generate_auth_document,
        db_session
    ):
        """
        Even if everything looks good, registration can fail if the library registry
        can't send out the validation emails.
        """
        # Simulate an SMTP server that won't accept email for whatever reason.
        class NonfunctionalEmailer(MockEmailer):
            def send(self, *args, **kwargs):
                raise SMTPException("SMTP server is broken")

        mock_registry_controller.emailer = NonfunctionalEmailer()

        # Pretend we are a library with a valid authentication document.
        auth_document = generate_auth_document(None)
        http_client.queue_response(200, content=json.dumps(auth_document), url=auth_document['id'])
        self.queue_opds_success(http_client)

        auth_url = "http://circmanager.org/authentication.opds"     # noqa: F841
        # Send a registration request to the registry.
        with app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict([
                ("url", auth_document['id']),
                ("contact", "mailto:me@library.org"),
            ])
            response = mock_registry_controller.register(do_get=http_client.do_get)

        # We get back a ProblemDetail the first time
        # we got a problem sending an email. In this case, it was
        # trying to contact the library's 'help' address included in the
        # library's authentication document.
        assert response.uri == INTEGRATION_ERROR.uri
        assert response.detail == "SMTP error while sending email to mailto:help@library.org"

        [test_library_to_destroy] = db_session.query(Library).filter(Library.name == 'A Library').all()

    def test_registration_fails_if_email_server_unusable(
        self, db_session, mock_registry_controller, http_client, app, generate_auth_document,
    ):
        """
        GIVEN: An email integration which is missing or not responding
        WHEN:  A registration is requested
        THEN:  A ProblemDetail of an appropriate type should be returned
        """
        # Simulate an SMTP server that is wholly unresponsive
        class UnresponsiveEmailer(Emailer):
            def _send_email(*args):
                raise Exception("message from UnresponsiveEmailer")

        unresponsive_emailer_kwargs = {
            "smtp_username": "library",
            "smtp_password": "library",
            "smtp_host": "library",
            "smtp_port": "12345",
            "from_name": "Test",
            "from_address": "test@library.tld",
            "templates": {
                "address_needs_confirmation": EmailTemplate(
                    "subject", "Hello, %(to_address)s, this is %(from_address)s."
                ),
                "address_designated": EmailTemplate(
                    "subject", "Hello, %(to_address)s, this is %(from_address)s."
                )
            },
        }

        mock_registry_controller.emailer = UnresponsiveEmailer(**unresponsive_emailer_kwargs)

        # Pretend we are a library with a valid authentication document.
        auth_document = generate_auth_document(None)
        http_client.queue_response(200, content=json.dumps(auth_document), url=auth_document['id'])
        self.queue_opds_success(http_client)

        # Send a registration request to the registry.
        with app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict([
                ("url", auth_document['id']),
                ("contact", "mailto:me@library.org"),
            ])
            response = mock_registry_controller.register(do_get=http_client.do_get)

        # We get back a ProblemDetail the first time
        # we got a problem sending an email. In this case, it was
        # trying to contact the library's 'help' address included in the
        # library's authentication document.
        assert response.uri == UNABLE_TO_NOTIFY.uri

        [test_library_to_destroy] = db_session.query(Library).filter(Library.name == 'A Library').all()

    @pytest.mark.needsdecomposition
    def test_register_success(
        self, db_session, app, mock_registry_controller, http_client, generate_auth_document,
        kansas_state, connecticut_state
    ):
        opds_directory = "application/opds+json;profile=https://librarysimplified.org/rel/profile/directory"

        # Pretend we are a library with a valid authentication document.
        key = RSA.generate(1024)
        auth_document = generate_auth_document(key)
        http_client.queue_response(200, content=json.dumps(auth_document), url=auth_document['id'])
        self.queue_opds_success(http_client)

        auth_url = "http://circmanager.org/authentication.opds"
        opds_url = "http://circmanager.org/feed/"

        # Send a registration request to the registry.
        random.seed(42)

        with app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict([
                ("url", auth_url),
                ("contact", "mailto:me@library.org"),
            ])
            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.status_code == 201
            assert response.headers.get("Content-Type") == opds_directory

            # The library has been created. Information from its
            # authentication document has been added to the database.
            library = get_one(db_session, Library, opds_url=opds_url)
            assert library is not None
            assert library.name == "A Library"
            assert library.description == "Description"
            assert library.web_url == "http://circmanager.org"
            assert library.logo == "data:image/png;imagedata"

            # The client didn't specify a stage, so the server acted
            # like the client asked to be put into production.
            assert library.library_stage == Library.PRODUCTION_STAGE

            assert library.anonymous_access is True
            assert library.online_registration is True

            [collection_summary] = library.collections
            assert collection_summary.language is None
            assert collection_summary.size == 100
            [service_area] = library.service_areas
            assert service_area.place_id == kansas_state.id

            # To get this information, a request was made to the
            # circulation manager's Authentication For OPDS document.
            # A follow-up request was made to the feed mentioned in that
            # document.
            #
            assert http_client.requests == [
                "http://circmanager.org/authentication.opds",
                "http://circmanager.org/feed/"
            ]

            # And the document we queued up was fed into the library
            # registry.
            catalog = json.loads(response.data)
            assert catalog['metadata']['title'] == "A Library"
            assert catalog['metadata']['description'] == 'Description'

            # Since the auth document had a public key, the registry
            # generated a short name and shared secret for the library.
            #
            # We know which short name will be generated because we seeded
            # the random number generator for this test.
            #
            # We can't try the same trick with the shared secret,
            # because it was generated using techniques designed for
            # cryptography which ignore seed(). But we do know how
            # long it is.
            expect = 'UDAXIH'
            assert expect == library.short_name
            assert len(library.shared_secret) == 48

            assert catalog["metadata"]["short_name"] == library.short_name
            # The registry encrypted the secret with the public key, and
            # it can be decrypted with the private key.
            encryptor = PKCS1_OAEP.new(key)
            shared_secret = catalog["metadata"]["shared_secret"]
            encrypted_secret = base64.b64decode(shared_secret.encode("utf8"))
            decrypted_secret = encryptor.decrypt(encrypted_secret)
            assert decrypted_secret.decode("utf8") == library.shared_secret

        old_secret = library.shared_secret
        http_client.requests = []

        # Hyperlink objects were created for the three email addresses
        # associated with the library.
        help_link, copyright_agent_link, integration_contact_link = sorted(
            library.hyperlinks, key=lambda x: x.rel
        )
        assert help_link.rel == "help"
        assert help_link.href == "mailto:help@library.org"
        assert copyright_agent_link.rel == Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL
        assert copyright_agent_link.href == "mailto:dmca@library.org"
        assert integration_contact_link.rel == Hyperlink.INTEGRATION_CONTACT_REL
        assert integration_contact_link.href == "mailto:me@library.org"

        # A confirmation email was sent out for each of those addresses.
        sent = sorted(mock_registry_controller.emailer.sent_out, key=lambda x: x[1])
        for email in sent:
            assert email[0] == Emailer.ADDRESS_NEEDS_CONFIRMATION
        destinations = [x[1] for x in sent]
        assert destinations == ["dmca@library.org", "help@library.org", "me@library.org"]
        mock_registry_controller.emailer.sent_out = []

        # The document sent by the library registry to the library
        # includes status information about the library's integration
        # contact address -- information that wouldn't be made
        # available to the public.
        [link] = [x for x in catalog['links'] if
                  x.get('rel') == Hyperlink.INTEGRATION_CONTACT_REL]
        assert link['href'] == "mailto:me@library.org"
        assert link['properties'][Validation.STATUS_PROPERTY] == Validation.IN_PROGRESS

        # Later, the library's information changes.
        auth_document = {
            "id": auth_url,
            "name": "A Library",
            "service_description": "New and improved",
            "links": [
                {"rel": "logo", "href": "/logo.png", "type": "image/png"},
                {"rel": "start", "href": "http://circmanager.org/feed/",
                 "type": "application/atom+xml;profile=opds-catalog"},
                {"rel": "help", "href": "mailto:new-help@library.org"},
                {"rel": "http://librarysimplified.org/rel/designated-agent/copyright", "href": "mailto:me@library.org"},

            ],
            "service_area": {"US": "Connecticut"},
        }
        http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document['id']
        )
        self.queue_opds_success(http_client)

        # We have a new logo as well.
        image_data = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x01\x03\x00\x00\x00%\xdbV'
            b'\xca\x00\x00\x00\x06PLTE\xffM\x00\x01\x01\x01\x8e\x1e\xe5\x1b\x00\x00\x00\x01tRNS\xcc\xd24V'
            b'\xfd\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        http_client.queue_response(200, content=image_data, media_type="image/png")

        # So the library re-registers itself, and gets an updated
        # registry entry.
        #
        # This time, the library explicitly specifies which stage it
        # wants to be in.
        with app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict([
                ("url", auth_url),
                ("contact", "mailto:me@library.org"),
                ("stage", Library.TESTING_STAGE)
            ])

            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.status_code == 200
            assert response.headers.get("Content-Type") == opds_directory

            # The data sent in the response includes the library's new
            # data.
            catalog = json.loads(response.data)
            assert catalog['metadata']['title'] == "A Library"
            assert catalog['metadata']['description'] == 'New and improved'

            # The library's new data is also in the database.
            library = get_one(db_session, Library, opds_url=opds_url)
            assert library is not None
            assert library.name == "A Library"
            assert library.description == "New and improved"
            assert library.web_url is None
            encoded_image = base64.b64encode(image_data).decode("utf8")
            assert library.logo == "data:image/png;base64,%s" % encoded_image
            # The library's library_stage has been updated to reflect
            # the 'stage' method passed in from the client.
            assert library.library_stage == Library.TESTING_STAGE

            # There are still three Hyperlinks associated with the
            # library.
            help_link_2, copyright_agent_link_2, integration_contact_link_2 = sorted(
                library.hyperlinks, key=lambda x: x.rel
            )

            # The Hyperlink objects are the same as before.
            assert help_link == help_link_2
            assert copyright_agent_link == copyright_agent_link_2
            assert integration_contact_link == integration_contact_link_2

            # But two of the hrefs have been updated to reflect the new
            # authentication document.
            assert help_link.rel == "help"
            assert help_link.href == "mailto:new-help@library.org"
            assert copyright_agent_link.rel == Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL
            assert copyright_agent_link.href == "mailto:me@library.org"

            # The link that hasn't changed is unaffected.
            assert integration_contact_link.rel == Hyperlink.INTEGRATION_CONTACT_REL
            assert integration_contact_link.href == "mailto:me@library.org"

            # Two emails were sent out -- one asking for confirmation
            # of new-help@library.org, and one announcing the new role
            # for me@library.org (which already has an outstanding
            # confirmation request) as designated copyright agent.
            new_dmca, new_help = sorted(
                [(x[1], x[0]) for x in mock_registry_controller.emailer.sent_out]
            )
            assert new_dmca == ("me@library.org", Emailer.ADDRESS_DESIGNATED)
            assert new_help == ("new-help@library.org", Emailer.ADDRESS_NEEDS_CONFIRMATION)

            # Commit to update library.service_areas.
            db_session.commit()

            # The library's service areas have been updated.
            [service_area] = library.service_areas
            assert service_area.place_id == connecticut_state.id

            # In addition to making the request to get the
            # Authentication For OPDS document, and the request to
            # get the root OPDS feed, the registry made a
            # follow-up request to download the library's logo.
            assert http_client.requests == [
                "http://circmanager.org/authentication.opds",
                "http://circmanager.org/feed/",
                "http://circmanager.org/logo.png"
            ]

        # If we include the old secret in a request and also set
        # reset_shared_secret, the registry will generate a new
        # secret.
        form_args_no_reset = ImmutableMultiDict([
            ("url", "http://circmanager.org/authentication.opds"),
            ("contact", "mailto:me@library.org")
        ])
        form_args_with_reset = ImmutableMultiDict(
            list(form_args_no_reset.items()) + [
                ("reset_shared_secret", "y")
            ]
        )
        with app.test_request_context("/", headers={"Authorization": "Bearer %s" % old_secret}, method="POST"):
            flask.request.form = form_args_with_reset
            key = RSA.generate(1024)
            auth_document = generate_auth_document(key)
            http_client.queue_response(
                200, content=json.dumps(auth_document), url=auth_document['id']
            )
            self.queue_opds_success(http_client)

            response = mock_registry_controller.register(do_get=http_client.do_get)
            assert response.status_code == 200
            catalog = json.loads(response.data)
            assert library.shared_secret != old_secret

            # The registry encrypted the new secret with the public key, and
            # it can be decrypted with the private key.
            encryptor = PKCS1_OAEP.new(key)
            encrypted_secret = base64.b64decode(catalog["metadata"]["shared_secret"])
            assert encryptor.decrypt(encrypted_secret).decode("utf8") == library.shared_secret

        old_secret = library.shared_secret

        # If we include an incorrect secret, or we don't ask for the
        # secret to be reset, the secret doesn't change.
        for secret, form in (
            ("notthesecret", form_args_with_reset),
            (library.shared_secret, form_args_no_reset)
        ):
            with app.test_request_context("/", headers={"Authorization": "Bearer %s" % secret}):
                flask.request.form = form

                key = RSA.generate(1024)
                auth_document = generate_auth_document(key)
                http_client.queue_response(
                    200, content=json.dumps(auth_document)
                )
                self.queue_opds_success(http_client)

                response = mock_registry_controller.register(
                    do_get=http_client.do_get
                )

                assert response.status_code == 200
                assert library.shared_secret == old_secret

        [test_library_to_destroy] = db_session.query(Library).filter(Library.name == 'A Library').all()

    def test_register_with_secret_changes_authentication_url_and_opds_url(
        self, db_session, create_test_library, http_client, app,
        mock_registry_controller, generate_auth_document
    ):
        # This Library was created previously with a certain shared
        # secret, at a URL that's no longer valid.
        secret = "it's a secret"
        library = create_test_library(db_session)
        library.authentication_url = "http://old-url/authentication_document"
        library.opds_url = "http://old-url/opds"
        library.shared_secret = secret

        # We're going to register a library at an apparently new URL,
        # but since we're providing the shared secret for an existing
        # Library, the registry will know to modify that Library instead
        # of creating a new one.
        auth_document = generate_auth_document()
        new_auth_url = auth_document['id']
        [new_opds_url] = [x['href'] for x in auth_document['links'] if x['rel'] == 'start']
        http_client.queue_response(200, content=json.dumps(auth_document), url=new_auth_url)
        self.queue_opds_success(http_client)

        with app.test_request_context("/", method="POST"):
            flask.request.headers = {"Authorization": "Bearer %s" % secret}
            flask.request.form = ImmutableMultiDict([("url", new_auth_url)])
            response = mock_registry_controller.register(do_get=http_client.do_get)
            # No new library was created.
            assert response.status_code == 200

        # The library's authentication_url and opds_url have been modified.
        assert library.authentication_url == new_auth_url
        assert library.opds_url == new_opds_url


class TestValidationController:
    def test_html_response(self, mock_registry):
        # Test the generation of a simple HTML-based HTTP response.
        controller = ValidationController(mock_registry)
        response = controller.html_response(999, "a message")
        assert response.status_code == 999
        assert response.headers['Content-Type'] == "text/html"
        assert response.data.decode("utf8") == controller.MESSAGE_TEMPLATE % dict(message="a message")

    def test_validate(self, mock_registry, db_session, create_test_library):
        class Mock(ValidationController):
            def html_response(self, status_code, message):
                return (status_code, message)

        controller = Mock(mock_registry)

        def assert_response(resource_id, secret, status_code, message):
            """Invoke the validate() method with the given secret
            and verify that html_response is called with the given
            status_code and message.
            """
            result = controller.confirm(resource_id, secret)
            assert result == (status_code, message)

        # This library has three links: two that are in the middle of
        # the validation process and one that has not started the
        # validation process.
        library = create_test_library(db_session)

        (link1, _) = library.set_hyperlink("rel", "mailto:1@library.org")
        needs_validation = link1.resource
        needs_validation.restart_validation()
        secret = needs_validation.validation.secret

        (link2, _) = library.set_hyperlink("rel2", "mailto:2@library.org")
        needs_validation_2 = link2.resource
        needs_validation_2.restart_validation()
        secret2 = needs_validation_2.validation.secret

        (link3, _) = library.set_hyperlink("rel2", "mailto:3@library.org")
        not_started = link3.resource    # noqa: F841

        # Simple tests for missing fields or failed lookups.
        assert_response(needs_validation.id, "", 404, "No confirmation code provided")
        assert_response(None, "a code", 404, "No resource ID provided")
        assert_response(-20, secret, 404, "No such resource")

        # Secret does not exist.
        assert_response(needs_validation.id, "nosuchcode", 404, "Confirmation code 'nosuchcode' not found")

        # Secret exists but is associated with a different Resource.
        assert_response(needs_validation.id, secret2, 404, "Confirmation code %r not found" % secret2)

        # Secret exists but is not associated with any Resource (this
        # shouldn't happen).
        needs_validation_2.validation.resource = None
        assert_response(needs_validation.id, secret2, 404, "Confirmation code %r not found" % secret2)

        # Secret matches resource but validation has expired.
        needs_validation.validation.started_at = (datetime.datetime.now() - datetime.timedelta(days=7))
        assert_response(
            needs_validation.id, secret, 400,
            "Confirmation code %r has expired. Re-register to get another code." % secret
        )

        # Success.
        needs_validation.restart_validation()
        secret = needs_validation.validation.secret
        assert_response(needs_validation.id, secret, 200, "You successfully confirmed mailto:1@library.org.")

        # A Resource can't be validated twice.
        assert_response(needs_validation.id, secret, 200, "This URI has already been validated.")


class TestCoverageController:
    def parse_to(
        self, app, db_session_obj, controller, coverage, places=[],
        ambiguous=None, unknown=None, to_json=True
    ):
        """
        Make a request to the coverage controller to turn a coverage object into GeoJSON.
        Verify that the Places in `places` are represented in the coverage object and that
        the 'ambiguous' and 'unknown' extensions are also as expected.
        """
        if to_json:
            coverage = json.dumps(coverage)
        with app.test_request_context("/?coverage=%s" % coverage, method="POST"):
            response = controller.lookup()

        # The response is always GeoJSON.
        assert response.headers['Content-Type'] == "application/geo+json"
        geojson = json.loads(response.data)

        # Unknown or ambiguous places will be mentioned in these extra fields.
        actual_unknown = geojson.pop('unknown', None)
        assert unknown == actual_unknown
        actual_ambiguous = geojson.pop('ambiguous', None)
        assert actual_ambiguous == ambiguous

        # Without those extra fields, the GeoJSON document should be
        # identical to the one we get by calling Place.to_geojson
        # on the expected places.
        expect_geojson = Place.to_geojson(db_session_obj, *places)
        assert expect_geojson == geojson

    def test_lookup(self, app, db_session, mock_registry, kansas_state, massachusetts_state, boston_ma):
        controller = CoverageController(mock_registry)
        # Set up a default nation to make it easier to test a variety of coverage area types.
        ConfigurationSetting.sitewide(db_session, Configuration.DEFAULT_NATION_ABBREVIATION).value = "US"

        # Parse some strings to GeoJSON objects.
        self.parse_to(app, db_session, controller, "Boston, MA", [boston_ma], to_json=False)
        self.parse_to(app, db_session, controller, "Boston, MA", [boston_ma], to_json=True)
        self.parse_to(app, db_session, controller, "Massachusetts", [massachusetts_state])
        self.parse_to(app, db_session, controller, ["Massachusetts", "Kansas"], [massachusetts_state, kansas_state])
        self.parse_to(app, db_session, controller, {"US": "Kansas"}, [kansas_state])
        self.parse_to(app, db_session, controller, {"US": ["Massachusetts", "Kansas"]},
                      [massachusetts_state, kansas_state])
        self.parse_to(app, db_session, controller, ["KS", "UT"], [kansas_state], unknown={"US": ["UT"]})

        # Creating two states with the same name is the simplest way
        # to create an ambiguity problem.
        massachusetts_state.external_name = "Kansas"
        self.parse_to(app, db_session, controller, "Kansas", [], ambiguous={"US": ["Kansas"]})

    def test_library_eligibility_and_focus(
        self, db_session, create_test_library, app, new_york_state, new_york_city
    ):
        # focus_for_library() and eligibility_for_library() represent a library's service area as GeoJSON.

        # We don't use self.nypl here because we want to set more realistic service and focus areas.
        library = create_test_library(db_session, library_name="NYPL")

        # New York State is the eligibility area for NYPL.
        get_one_or_create(
            db_session, ServiceArea, library=library,
            place=new_york_state, type=ServiceArea.ELIGIBILITY
        )

        # New York City is the focus area.
        get_one_or_create(
            db_session, ServiceArea, library=library,
            place=new_york_city, type=ServiceArea.FOCUS
        )

        with app.test_request_context("/"):
            flask.request.library = library
            focus = app.library_registry.coverage_controller.focus_for_library()
            eligibility = app.library_registry.coverage_controller.eligibility_for_library()

            # In both cases we got a GeoJSON document
            for response in (focus, eligibility):
                assert response.status_code == 200
                assert response.headers['Content-Type'] == "application/geo+json"

            # The GeoJSON documents are the ones we'd expect from turning
            # the corresponding service areas into GeoJSON.
            focus = json.loads(focus.data)
            assert focus == Place.to_geojson(db_session, new_york_city)

            eligibility = json.loads(eligibility.data)
            assert eligibility == Place.to_geojson(db_session, new_york_state)

class TestAdminController:

    def _is_library(self, expected, actual, has_email=True):
        # Helper method to check that a library found by a controller is equivalent
        # to a particular library in the database
        flattened = {}
        # Getting rid of the "uuid" key before populating flattened, because its value
        # is just a string, not a subdictionary.
        # The UUID information is still being checked elsewhere.
        del actual["uuid"]
        for subdictionary in list(actual.values()):
            flattened.update(subdictionary)

        for k in flattened:
            if k == "library_stage":
                assert expected._library_stage == flattened.get("library_stage")
            elif k == "timestamp":
                actual_ts = flattened.get("timestamp")
                expected_ts = expected.timestamp
                actual_time = [actual_ts.year, actual_ts.month, actual_ts.day]
                expected_time = [expected_ts.year, expected_ts.month, expected_ts.day]
                assert expected_time == actual_time
            elif k.endswith("_email"):
                if has_email:
                    expected_email = expected.name + "@library.org"
                    assert expected_email == flattened.get(k)
            elif k.endswith("_validated"):
                if isinstance(flattened.get(k), str):
                    assert flattened.get(k) == "Not validated"
                elif isinstance(flattened.get(k), datetime.datetime):
                    continue
            elif k == "online_registration":
                assert str(expected.online_registration) == flattened.get("online_registration")
            elif k in ["focus", "service"]:
                area_type_names = dict(focus=ServiceArea.FOCUS, service=ServiceArea.ELIGIBILITY)
                actual_areas = flattened.get(k)
                expected_areas = [x.place.human_friendly_name or 'Everywhere'
                                  for x in expected.service_areas
                                  if x.type == area_type_names[k]]
                assert expected_areas == actual_areas
            elif k == Library.PLS_ID:
                assert expected.pls_id.value == flattened.get(k)
            elif k == "number_of_patrons":
                assert str(getattr(expected, k)) == flattened.get(k)
            else:
                assert getattr(expected, k) == flattened.get(k)

    def _check_keys(self, library):
        # Helper method to check that the controller is sending the right pieces of information about a library.
        expected_categories = ['uuid', 'basic_info', 'urls_and_contact', 'stages', 'areas']
        assert set(library.keys()) == set(expected_categories)

        expected_info_keys = ['name', 'short_name', 'description', 'timestamp', 'internal_urn',
                              'online_registration', 'pls_id', 'number_of_patrons']
        assert set(library.get("basic_info").keys()) == set(expected_info_keys)

        expected_url_contact_keys = ['contact_email', 'help_email', 'copyright_email', 'web_url',
                                     'authentication_url', 'contact_validated', 'help_validated',
                                     'copyright_validated', 'opds_url']
        assert set(library.get("urls_and_contact")) == set(expected_url_contact_keys)

        expected_area_keys = ['focus', 'service']
        assert set(library.get("areas")) == set(expected_area_keys)

        expected_stage_keys = ['library_stage', 'registry_stage']
        assert set(library.get("stages").keys()) == set(expected_stage_keys)

    def test_library_details(self, db_session, app, nypl, mock_admin_controller):
        # Test that the controller can look up the complete information for one specific library.
        def check(has_email=True):
            uuid = nypl.internal_urn.split("uuid:")[1]
            with app.test_request_context("/"):
                response = mock_admin_controller.library_details(uuid, 0)
            assert response.get("uuid") == uuid
            self._check_keys(response)
            self._is_library(nypl, response, has_email)

        check()

        # Delete the library's contact email, simulating an old
        # library created before this rule was instituted, and try
        # again.
        [db_session.delete(x) for x in nypl.hyperlinks]
        check(False)

    def test_library_details_with_error(self, app, mock_admin_controller):
        # Test that the controller returns a problem detail document if the requested library doesn't exist.
        uuid = "not a real UUID!"
        with app.test_request_context("/"):
            response = mock_admin_controller.library_details(uuid)

        assert isinstance(response, ProblemDetail)
        assert response.status_code == 404
        assert response.title == LIBRARY_NOT_FOUND.title
        assert response.uri == LIBRARY_NOT_FOUND.uri

    def test_edit_registration(
        self, db_session, create_test_library, app, mock_admin_controller
    ):
        # Test that a specific library's stages can be edited via submitting a form.
        library = create_test_library(db_session, library_name="Test Library", short_name="test_lib",
                                      library_stage=Library.CANCELLED_STAGE, registry_stage=Library.TESTING_STAGE)
        uuid = library.internal_urn.split("uuid:")[1]
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", uuid),
                ("Library Stage", "testing"),
                ("Registry Stage", "production"),
            ])

            response = mock_admin_controller.edit_registration()

        assert response._status_code == 200
        assert response.response[0].decode("utf8") == library.internal_urn

        edited_library = get_one(db_session, Library, short_name=library.short_name)
        assert edited_library.library_stage == Library.TESTING_STAGE
        assert edited_library.registry_stage == Library.PRODUCTION_STAGE

    def test_edit_registration_with_error(self, app, mock_admin_controller):
        uuid = "not a real UUID!"
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", uuid),
                ("Library Stage", "testing"),
                ("Registry Stage", "production"),
            ])
            response = mock_admin_controller.edit_registration()
        assert isinstance(response, ProblemDetail)
        assert response.status_code == 404
        assert response.title == LIBRARY_NOT_FOUND.title
        assert response.uri == LIBRARY_NOT_FOUND.uri

    def test_edit_registration_with_override(self, db_session, nypl, app, mock_admin_controller):
        # Normally, if a library is already in production, its library_stage cannot be edited.
        # Admins should be able to override this by using the interface.
        uuid = nypl.internal_urn.split("uuid:")[1]
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", uuid),
                ("Library Stage", "cancelled"),
                ("Registry Stage", "cancelled")
            ])

            response = mock_admin_controller.edit_registration()
            assert response._status_code == 200
            assert response.response[0].decode("utf8") == nypl.internal_urn
            edited_nypl = get_one(db_session, Library, internal_urn=nypl.internal_urn)    # noqa: F841

    def test_validate_email(self, app, mock_admin_controller, nypl):
        # You can't validate an email for a nonexistent library.
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", "no:such:library"),
                ("email", "contact_email")
            ])
            response = mock_admin_controller.validate_email()
        assert isinstance(response, ProblemDetail)
        assert response.status_code == 404
        assert response.title == LIBRARY_NOT_FOUND.title
        assert response.uri == LIBRARY_NOT_FOUND.uri

        uuid = nypl.internal_urn.split("uuid:")[1]
        validation = nypl.hyperlinks[0].resource.validation
        assert validation is None

        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", uuid),
                ("email", "contact_email")
            ])
            mock_admin_controller.validate_email()

        validation = nypl.hyperlinks[0].resource.validation
        assert isinstance(validation, Validation)
        assert validation.success is True

    def test_missing_email_error(
        self, db_session, create_test_library, app, mock_admin_controller
    ):
        library_without_email = create_test_library(db_session)
        uuid = library_without_email.internal_urn.split("uuid:")[1]
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", uuid),
                ("email", "contact_email")
            ])
            response = mock_admin_controller.validate_email()

        assert isinstance(response, ProblemDetail)
        assert response.status_code == 400
        assert response.detail == 'The contact URI for this library is missing or invalid'
        assert response.uri == 'http://librarysimplified.org/terms/problem/invalid-contact-uri'

    def test_add_or_edit_pls_id(self, db_session, nypl, app, mock_admin_controller):
        # Test that the user can input a new PLS ID
        assert nypl.pls_id.value is None
        uuid = nypl.internal_urn.split("uuid:")[1]
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", uuid),
                ("pls_id", "12345")
            ])
            response = mock_admin_controller.add_or_edit_pls_id()

        assert response._status_code == 200
        assert response.response[0].decode("utf8") == nypl.internal_urn

        library_with_pls_id = get_one(db_session, Library, short_name=nypl.short_name)
        assert library_with_pls_id.pls_id.value == "12345"

        # Test that the user can edit an existing PLS ID
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", uuid),
                ("pls_id", "abcde")
            ])
            response = mock_admin_controller.add_or_edit_pls_id()

        updated = get_one(db_session, Library, short_name=nypl.short_name)
        assert updated.pls_id.value == "abcde"

    def test_add_or_edit_pls_id_with_error(self, app, mock_admin_controller):
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", "abc"),
                ("pls_id", "12345")
            ])
            response = mock_admin_controller.add_or_edit_pls_id()
        assert response.status_code == 404
        assert response.uri == LIBRARY_NOT_FOUND.uri

    def test_search_details(
        self, db_session, create_test_library, app, mock_admin_controller,
        nypl, kansas_state_library, connecticut_state_library
    ):
        library = nypl
        kansas = kansas_state_library
        connecticut = connecticut_state_library
        with_description = create_test_library(db_session, library_name="Library With Description",
                                               has_email=True, description="For testing purposes")

        # Searching for the name of a real library returns a dict whose value is a list containing
        # that library.
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([("name", "NYPL")])
            response = mock_admin_controller.search_details()

        for response_library in response.get("libraries"):
            self._is_library(library, response_library)

        # Searching for part of the library's name--"kansas" instead of "kansas state library" works.
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([("name", "kansas")])
            response = mock_admin_controller.search_details()

        for response_library in response.get("libraries"):
            self._is_library(kansas, response_library)

        # Searching for a partial name may yield multiple results.
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([("name", "state")])
            response = mock_admin_controller.search_details()

        libraries = response.get("libraries")
        assert len(libraries) == 2

        ct_then_ks = sorted(libraries, key=lambda x: x['basic_info']['short_name'])
        self._is_library(connecticut, ct_then_ks[0])
        self._is_library(kansas, ct_then_ks[1])

        # Searching for a word or phrase found within a library's description returns
        # a dict whose value is a list containing that library.
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([("name", "testing")])
            response = mock_admin_controller.search_details()

        self._is_library(with_description, response.get("libraries")[0])

        # Searching for a name that cannot be found returns a problem detail.
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([("name", "other")])
            response = mock_admin_controller.search_details()

        assert response == LIBRARY_NOT_FOUND

    def test_log_in(self, app, mock_admin_controller):
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([("username", "Admin"), ("password", "123")])
            response = mock_admin_controller.log_in()
            assert response.status == "302 FOUND"
            assert session["username"] == "Admin"

    def test_log_in_with_error(self, db_session, app, mock_admin_controller):
        admin = Admin.authenticate(db_session, "Admin", "123")
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("username", "Admin"),
                ("password", "wrong"),
            ])
            response = mock_admin_controller.log_in()
            assert(isinstance(response, ProblemDetail))
            assert response.status_code == 401
            assert response.title == INVALID_CREDENTIALS.title
            assert response.uri == INVALID_CREDENTIALS.uri
        db_session.delete(admin)
        db_session.commit()

    def test_log_in_new_admin(self, app, mock_admin_controller):
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("username", "New"),
                ("password", "password")
            ])
            response = mock_admin_controller.log_in()
            assert response.status == "302 FOUND"
            assert session["username"] == "New"

    def test_log_out(self, db_session, app, mock_admin_controller):
        admin = Admin.authenticate(db_session, "Admin", "123")
        with app.test_request_context("/"):
            flask.request.form = MultiDict([("username", "Admin"), ("password", "123")])
            mock_admin_controller.log_in()

            assert session["username"] == "Admin"
            response = mock_admin_controller.log_out()
            assert session["username"] == ""
            assert response.status == "302 FOUND"
        db_session.delete(admin)
        db_session.commit()
