from nose.tools import (
    eq_,
    set_trace,
)
import datetime
import os
import json
import base64
import random
from smtplib import SMTPException
from urllib import unquote

from contextlib import contextmanager
from controller import (
    AdobeVendorIDController,
    BaseController,
    CoverageController,
    LibraryRegistry,
    LibraryRegistryAnnotator,
    LibraryRegistryController,
    ValidationController,
)

import flask
from flask import Response, session
from werkzeug import ImmutableMultiDict, MultiDict
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP

from . import DatabaseTest
from testing import DummyHTTPClient
from util import GeometryUtility
from util.problem_detail import ProblemDetail

from authentication_document import AuthenticationDocument
from emailer import Emailer
from opds import OPDSCatalog
from model import (
    create,
    get_one,
    get_one_or_create,
    ConfigurationSetting,
    ExternalIntegration,
    Hyperlink,
    Library,
    Place,
    ServiceArea,
    Validation,
)
from util.http import RequestTimedOut
from problem_details import *
from config import Configuration


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


class ControllerTest(DatabaseTest):
    def setup(self):
        from app import app, set_secret_key

        super(ControllerTest, self).setup()
        ConfigurationSetting.sitewide(self._db, Configuration.SECRET_KEY).value = "a secret"
        set_secret_key(self._db)

        os.environ['AUTOINITIALIZE'] = "False"
        del os.environ['AUTOINITIALIZE']
        self.app = app
        self.data_setup()
        self.library_registry = MockLibraryRegistry(
            self._db, testing=True, emailer_class=MockEmailer,
        )
        self.app.library_registry = self.library_registry
        self.http_client = DummyHTTPClient()

    def data_setup(self):
        """Configure the site before setup() creates a LibraryRegistry
        object.
        """
        pass

    def vendor_id_setup(self):
        """Configure a basic vendor id service."""
        integration, ignore = get_one_or_create(
            self._db, ExternalIntegration,
            protocol=ExternalIntegration.ADOBE_VENDOR_ID,
            goal=ExternalIntegration.DRM_GOAL,
        )
        integration.setting(Configuration.ADOBE_VENDOR_ID).value = "VENDORID"

    @contextmanager
    def request_context_with_library(self, route, *args, **kwargs):
        library = kwargs.pop('library')
        with self.app.test_request_context(route, *args, **kwargs) as c:
            flask.request.library = library
            yield c


class TestLibraryRegistryAnnotator(ControllerTest):
    def test_annotate_catalog(self):
        annotator = LibraryRegistryAnnotator(self.app.library_registry)

        integration, ignore = create(
            self._db, ExternalIntegration,
            protocol=ExternalIntegration.ADOBE_VENDOR_ID,
            goal=ExternalIntegration.DRM_GOAL,
        )
        integration.setting(Configuration.ADOBE_VENDOR_ID).value = "VENDORID"

        with self.app.test_request_context("/"):
            catalog = OPDSCatalog(self._db, "Test Catalog", "http://catalog", [])
            annotator.annotate_catalog(catalog)

            # The catalog should have three new links: search, register, and a templated link
            # for a library's OPDS entry, in addition to self. It should also have the adobe
            # vendor id in the catalog's metadata.

            links = catalog.catalog.get("links")
            eq_(4, len(links))
            [opds_link, register_link, search_link, self_link] = sorted(links, key=lambda x: x.get("rel"))

            eq_('http://localhost/library/{uuid}', opds_link.get("href"))
            eq_('http://librarysimplified.org/rel/registry/library', opds_link.get("rel"))
            eq_('application/opds+json', opds_link.get("type"))
            eq_(True, opds_link.get("templated"))

            eq_('http://localhost/search', search_link.get("href"))
            eq_("search", search_link.get("rel"))
            eq_('application/opensearchdescription+xml', search_link.get("type"))

            eq_('http://localhost/register', register_link.get("href"))
            eq_('register', register_link.get('rel'))
            eq_('application/opds+json;profile=https://librarysimplified.org/rel/profile/directory', register_link.get('type'))

            eq_("VENDORID", catalog.catalog.get("metadata").get('adobe_vendor_id'))


class TestBaseController(ControllerTest):

    def test_library_for_request(self):
        # Test the code that looks up a library by its UUID and
        # sets it as flask.request.library.
        controller = BaseController(self.library_registry)
        f = controller.library_for_request
        library = self._library()

        with self.app.test_request_context("/"):
            eq_(LIBRARY_NOT_FOUND, f(None))
            eq_(LIBRARY_NOT_FOUND, f("no such uuid"))

            eq_(library, f(library.internal_urn))
            eq_(library, flask.request.library)

            flask.request.library = None
            eq_(library, f(library.internal_urn[len("urn:uuid:"):]))
            eq_(library, flask.request.library)


class TestLibraryRegistry(ControllerTest):

    def test_instantiated_controllers(self):
        # Verify that the controllers were instantiated and attached
        # to the LibraryRegistry object.
        assert isinstance(
            self.library_registry.registry_controller,
            LibraryRegistryController
        )
        assert isinstance(
            self.library_registry.validation_controller,
            ValidationController
        )

        # No Adobe Vendor ID was set up.
        eq_(None, self.library_registry.adobe_vendor_id)

        # Let's configure one.
        self.vendor_id_setup()
        registry_with_adobe = MockLibraryRegistry(
            self._db, testing=True, emailer_class=MockEmailer
        )
        assert isinstance(
            registry_with_adobe.adobe_vendor_id,
            AdobeVendorIDController
        )


class TestLibraryRegistryController(ControllerTest):

    def data_setup(self):
        """Configure the site before setup() creates a LibraryRegistry
        object.
        """
        # Create some places and libraries.
        nypl = self.nypl
        ct_state = self.connecticut_state_library
        ks_state = self.kansas_state_library

        nyc = self.new_york_city
        boston = self.boston_ma
        manhattan_ks = self.manhattan_ks
        us = self.crude_us

        self.vendor_id_setup()

    def setup(self):
        super(TestLibraryRegistryController, self).setup()
        self.controller = LibraryRegistryController(
            self.library_registry, emailer_class=MockEmailer
        )

        # A registration form that's valid for most of the tests
        # in this class.
        self.registration_form = ImmutableMultiDict([
            ("url", "http://circmanager.org/authentication.opds"),
            ("contact", "mailto:integrationproblems@library.org"),
        ])

        # Turn some places into geographic points.
        self.manhattan = GeometryUtility.point_from_ip("65.88.88.124")
        self.oakland = GeometryUtility.point_from_string("37.8,-122.2")

    def _is_library(self, expected, actual, has_email=True):
        # Helper method to check that a library found by a controller is equivalent to a particular library in the database
        flattened = {}
        # Getting rid of the "uuid" key before populating flattened, because its value is just a string, not a subdictionary.
        # The UUID information is still being checked elsewhere.
        del actual["uuid"]
        for subdictionary in actual.values():
            flattened.update(subdictionary)

        for k in flattened:
            if k == "library_stage":
                eq_(flattened.get("library_stage"), expected._library_stage)
            elif k == "timestamp":
                actual_ts = flattened.get("timestamp")
                expected_ts = expected.timestamp
                actual_time = [actual_ts.year, actual_ts.month, actual_ts.day]
                expected_time = [expected_ts.year, expected_ts.month, expected_ts.day]
                eq_(actual_time, expected_time)
            elif k == "contact_email":
                if has_email:
                    expected_contact_email = expected.name + "@library.org"
                    eq_(flattened.get("contact_email"), expected_contact_email)
            elif k == "validated":
                eq_(flattened.get("validated"), "Not validated")
            elif k == "online_registration":
                eq_(flattened.get("online_registration"), str(expected.online_registration))
            else:
                eq_(flattened.get(k), getattr(expected, k))

    def _check_keys(self, library):
        # Helper method to check that the controller is sending the right pieces of information about a library.

        expected_categories = ['uuid', 'basic_info', 'urls_and_contact', 'stages']
        eq_(set(expected_categories), set(library.keys()))

        expected_info_keys = ['name', 'short_name', 'description', 'timestamp', 'internal_urn', 'online_registration']
        eq_(set(expected_info_keys), set(library.get("basic_info").keys()))

        expected_url_contact_keys = ['contact_email', 'web_url', 'authentication_url', 'validated', 'opds_url']
        eq_(set(expected_url_contact_keys), set(library.get("urls_and_contact")))

        expected_stage_keys = ['library_stage', 'registry_stage']
        eq_(set(expected_stage_keys), set(library.get("stages").keys()))


    def test_libraries(self):
        # Test that the controller returns a specific set of information for each library.
        ct = self.connecticut_state_library
        ks = self.kansas_state_library
        nypl = self.nypl

        response = self.controller.libraries()
        libraries = response.get("libraries")

        eq_(len(libraries), 3)
        for library in libraries:
            self._check_keys(library)

        expected_names = [expected.name for expected in [ct, ks, nypl]]
        actual_names = [library.get("basic_info").get("name") for library in libraries]
        eq_(set(expected_names), set(actual_names))

        self._is_library(ct, libraries[0])
        self._is_library(ks, libraries[1])
        self._is_library(nypl, libraries[2])

    def test_library_details(self):
        # Test that the controller can look up the complete information for one specific library.
        library = self.nypl

        def check(has_email=True):
            uuid = library.internal_urn.split("uuid:")[1]
            with self.app.test_request_context("/"):
                response = self.controller.library_details(uuid)
            eq_(uuid, response.get("uuid"))
            self._check_keys(response)
            self._is_library(library, response, has_email)

        check()

        # Delete the library's contact email, simulating an old
        # library created before this rule was instituted, and try
        # again.
        [self._db.delete(x) for x in library.hyperlinks]
        check(False)

    def test_library_details_with_error(self):
        # Test that the controller returns a problem detail document if the requested library doesn't exist.
        uuid = "not a real UUID!"
        with self.app.test_request_context("/"):
            response = self.controller.library_details(uuid)

        assert isinstance(response, ProblemDetail)
        eq_(response.status_code, 404)
        eq_(response.title, LIBRARY_NOT_FOUND.title)
        eq_(response.uri, LIBRARY_NOT_FOUND.uri)

    def test_edit_registration(self):
        # Test that a specific library's stages can be edited via submitting a form.
        library = self._library(
            name="Test Library",
            short_name="test_lib",
            library_stage=Library.CANCELLED_STAGE,
            registry_stage=Library.TESTING_STAGE
        )
        uuid = library.internal_urn.split("uuid:")[1]
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", uuid),
                ("Library Stage", "testing"),
                ("Registry Stage", "production"),
            ])

            response = self.controller.edit_registration()

        eq_(response._status_code, 200)
        eq_(response.response[0], library.internal_urn)

        edited_library = get_one(self._db, Library, short_name=library.short_name)
        eq_(edited_library.library_stage, Library.TESTING_STAGE)
        eq_(edited_library.registry_stage, Library.PRODUCTION_STAGE)

    def test_edit_registration_with_error(self):
        uuid = "not a real UUID!"
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", uuid),
                ("Library Stage", "testing"),
                ("Registry Stage", "production"),
            ])
            response = self.controller.edit_registration()
        assert isinstance(response, ProblemDetail)
        eq_(response.status_code, 404)
        eq_(response.title, LIBRARY_NOT_FOUND.title)
        eq_(response.uri, LIBRARY_NOT_FOUND.uri)

    def test_edit_registration_with_override(self):
        # Normally, if a library is already in production, its library_stage cannot be edited.
        # Admins should be able to override this by using the interface.
        nypl = self.nypl
        uuid = nypl.internal_urn.split("uuid:")[1]
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", uuid),
                ("Library Stage", "cancelled"),
                ("Registry Stage", "cancelled")
            ])

            response = self.controller.edit_registration()
            eq_(response._status_code, 200)
            eq_(response.response[0], nypl.internal_urn)
            edited_nypl = get_one(self._db, Library, internal_urn=nypl.internal_urn)

    def test_validate_email(self):
        nypl = self.nypl
        uuid = nypl.internal_urn.split("uuid:")[1]
        validation = nypl.hyperlinks[0].resource.validation
        eq_(validation, None)

        with self.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", uuid),
            ])
            self.controller.validate_email()

        validation = nypl.hyperlinks[0].resource.validation
        assert isinstance(validation, Validation)
        eq_(validation.success, True)

    def test_missing_email_error(self):
        library_without_email = self._library()
        uuid = library_without_email.internal_urn.split("uuid:")[1]
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", uuid),
            ])
            response = self.controller.validate_email()

        assert isinstance(response, ProblemDetail)
        eq_(response.status_code, 400)
        eq_(response.detail, 'The contact URI for this library is missing or invalid')
        eq_(response.uri, 'http://librarysimplified.org/terms/problem/invalid-contact-uri')

    def _log_in(self):
        flask.request.form = MultiDict([
            ("username", "Admin"),
            ("password", "123"),
        ])
        return self.controller.log_in()

    def test_log_in(self):
        with self.app.test_request_context("/", method="POST"):
            response = self._log_in()
            eq_(response.status, "302 FOUND")
            eq_(session["username"], "Admin")

    def test_log_in_with_error(self):
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("username", "wrong"),
                ("password", "abc"),
            ])
            response = self.controller.log_in()
            assert(isinstance(response, ProblemDetail))
            eq_(response.status_code, 401)
            eq_(response.title, INVALID_CREDENTIALS.title)
            eq_(response.uri, INVALID_CREDENTIALS.uri)

    def test_log_out(self):
        with self.app.test_request_context("/"):
            self._log_in()
            eq_(session["username"], "Admin")
            response = self.controller.log_out();
            eq_(session["username"], "")
            eq_(response.status, "302 FOUND")

    def test_instantiate_without_emailer(self):
        """If there is no emailer configured, the controller will still start
        up.
        """
        controller = LibraryRegistryController(self.library_registry)
        eq_(None, controller.emailer)

    def test_nearby(self):
        with self.app.test_request_context("/"):
            response = self.controller.nearby(self.manhattan, live=True)
            assert isinstance(response, Response)
            eq_("200 OK", response.status)
            eq_(OPDSCatalog.OPDS_TYPE, response.headers['Content-Type'])
            catalog = json.loads(response.data)

            # The catalog can be cached for a while, since the list of libraries
            # doesn't change very quickly.
            eq_("public, no-transform, max-age: 43200, s-maxage: 21600",
                response.headers['Cache-Control'])

            # We found both libraries within a 150-kilometer radius of the
            # starting point.
            nypl, ct = catalog['catalogs']
            eq_("NYPL", nypl['metadata']['title'])
            eq_("0 km.", nypl['metadata']['distance'])
            eq_("Connecticut State Library", ct['metadata']['title'])
            eq_("35 km.", ct['metadata']['distance'])

            # If that's not good enough, there's a link to the search
            # controller, so you can do a search.
            [library_link, register_link, search_link, self_link] = sorted(
                catalog['links'], key=lambda x: x['rel']
            )
            url_for = self.app.library_registry.url_for

            eq_(url_for("nearby"), self_link['href'])
            eq_("self", self_link['rel'])
            eq_(OPDSCatalog.OPDS_TYPE, self_link['type'])

            eq_(url_for("search"), search_link['href'])
            eq_("search", search_link['rel'])
            eq_("application/opensearchdescription+xml", search_link['type'])

            eq_(url_for("register"), register_link["href"])
            eq_("register", register_link["rel"])
            eq_("application/opds+json;profile=https://librarysimplified.org/rel/profile/directory", register_link["type"])

            eq_(unquote(url_for("library", uuid="{uuid}")), library_link["href"])
            eq_("http://librarysimplified.org/rel/registry/library", library_link["rel"])
            eq_("application/opds+json", library_link["type"])
            eq_(True, library_link.get("templated"))

            eq_("VENDORID", catalog["metadata"]["adobe_vendor_id"])

    def test_nearby_qa(self):
        # The libraries we used in the previous test are in production.
        # If we move them from production to TESTING, we won't find anything.
        for library in self._db.query(Library):
            library.registry_stage = Library.TESTING_STAGE
        with self.app.test_request_context("/"):
            response = self.controller.nearby(self.manhattan, live=True)
            catalogs = json.loads(response.data)
            eq_([], catalogs['catalogs'])

        # However, they will show up in the QA feed.
        with self.app.test_request_context("/"):
            response = self.controller.nearby(self.manhattan, live=False)
            catalogs = json.loads(response.data)
            eq_(2, len(catalogs['catalogs']))
            [catalog] = [
                x for x in catalogs['catalogs']
                if x['metadata']['id'] == self.nypl.internal_urn
            ]
            assert("", catalog['metadata']['title'])

            # Some of the links are the same as in the production feed;
            # others are different.
            url_for = self.app.library_registry.url_for
            [library_link, register_link, search_link, self_link] = sorted(
                catalogs['links'], key=lambda x: x['rel']
            )

            # The 'register' link is the same as in the main feed.
            eq_(url_for("register"), register_link["href"])
            eq_("register", register_link["rel"])

            # So is the 'library' templated link.
            eq_(unquote(url_for("library", uuid="{uuid}")), library_link["href"])
            eq_("http://librarysimplified.org/rel/registry/library", library_link["rel"])

            # This is a QA feed, and the 'search' and 'self' links
            # will give results from the QA feed.
            eq_(url_for("nearby_qa"), self_link['href'])
            eq_("self", self_link['rel'])

            eq_(url_for("search_qa"), search_link['href'])
            eq_("search", search_link['rel'])

    def test_nearby_no_location(self):
        with self.app.test_request_context("/"):
            response = self.controller.nearby(None)
            assert isinstance(response, Response)
            eq_("200 OK", response.status)
            eq_(OPDSCatalog.OPDS_TYPE, response.headers['Content-Type'])
            catalogs = json.loads(response.data)

            # We found no nearby libraries, because we had no location to
            # start with.
            eq_([], catalogs['catalogs'])

    def test_nearby_no_libraries(self):
        with self.app.test_request_context("/"):
            response = self.controller.nearby(self.oakland)
            assert isinstance(response, Response)
            eq_("200 OK", response.status)
            eq_(OPDSCatalog.OPDS_TYPE, response.headers['Content-Type'])
            catalog = json.loads(response.data)

            # We found no nearby libraries, because we were across the
            # country from the only ones in the registry.
            eq_([], catalog['catalogs'])

    def test_search_form(self):
        with self.app.test_request_context("/"):
            response = self.controller.search(None)
            eq_("200 OK", response.status)
            eq_("application/opensearchdescription+xml",
                response.headers['Content-Type'])

            # The search form can be cached more or less indefinitely.
            eq_("public, no-transform, max-age: 2592000",
                response.headers['Cache-Control'])

            # The search form points the client to the search controller.
            expect_url = self.library_registry.url_for("search")
            expect_url_tag = '<Url type="application/atom+xml;profile=opds-catalog" template="%s?q={searchTerms}"/>' % expect_url
            assert expect_url_tag in response.data

    def test_qa_search_form(self):
        """The QA search form links to the QA search controller."""
        with self.app.test_request_context("/"):
            response = self.controller.search(None, live=False)
            eq_("200 OK", response.status)

            expect_url = self.library_registry.url_for("search_qa")
            expect_url_tag = '<Url type="application/atom+xml;profile=opds-catalog" template="%s?q={searchTerms}"/>' % expect_url
            assert expect_url_tag in response.data

    def test_search(self):
        with self.app.test_request_context("/?q=manhattan"):
            response = self.controller.search(self.manhattan)
            eq_("200 OK", response.status)
            eq_(OPDSCatalog.OPDS_TYPE, response.headers['Content-Type'])
            catalog = json.loads(response.data)
            # We found the two matching results.
            [nypl, ks] = catalog['catalogs']
            eq_("NYPL", nypl['metadata']['title'])
            eq_("0 km.", nypl['metadata']['distance'])

            eq_("Kansas State Library", ks['metadata']['title'])
            eq_("1922 km.", ks['metadata']['distance'])

            [library_link, register_link, search_link, self_link] = sorted(
                catalog['links'], key=lambda x: x['rel']
            )
            url_for = self.app.library_registry.url_for

            # The search results have a self link and a link back to
            # the search form.
            eq_(url_for("search", q="manhattan"), self_link['href'])
            eq_("self", self_link['rel'])
            eq_(OPDSCatalog.OPDS_TYPE, self_link['type'])

            eq_(url_for("search"), search_link['href'])
            eq_("search", search_link['rel'])
            eq_("application/opensearchdescription+xml", search_link['type'])

            eq_(url_for("register"), register_link["href"])
            eq_("register", register_link["rel"])
            eq_("application/opds+json;profile=https://librarysimplified.org/rel/profile/directory", register_link["type"])

            eq_(unquote(url_for("library", uuid="{uuid}")), library_link["href"])
            eq_("http://librarysimplified.org/rel/registry/library", library_link["rel"])
            eq_("application/opds+json", library_link["type"])
            eq_(True, library_link.get("templated"))

            eq_("VENDORID", catalog["metadata"]["adobe_vendor_id"])

    def test_search_qa(self):
        # As we saw in the previous test, this search picks up two
        # libraries when we run it looking for production libraries. If
        # all of the libraries are cancelled, we don't find anything.
        for l in self._db.query(Library):
            eq_(l.registry_stage, Library.PRODUCTION_STAGE)

        for l in self._db.query(Library):
            l.registry_stage = Library.CANCELLED_STAGE
        with self.app.test_request_context("/?q=manhattan"):
            response = self.controller.search(self.manhattan, live=True)
            catalog = json.loads(response.data)
            eq_([], catalog['catalogs'])

        # If we move one of the libraries back into the PRODUCTION
        # stage, we find it.
        self.kansas_state_library.registry_stage = Library.PRODUCTION_STAGE
        with self.app.test_request_context("/?q=manhattan"):
            response = self.controller.search(self.manhattan, live=True)
            catalog = json.loads(response.data)
            [catalog] = catalog['catalogs']
            eq_('Kansas State Library', catalog['metadata']['title'])

    def test_library(self):
        nypl = self.nypl
        with self.request_context_with_library("/", library=nypl):
            response = self.controller.library()
        [catalog_entry] = json.loads(response.data).get("catalogs")
        eq_(nypl.name, catalog_entry.get("metadata").get("title"))
        eq_(nypl.internal_urn, catalog_entry.get("metadata").get("id"))

    def queue_opds_success(
            self, auth_url="http://circmanager.org/authentication.opds",
            media_type=None
    ):
        """The next HTTP request made by the registry will appear to retrieve
        a functional OPDS feed that links to `auth_url` as its
        Authentication For OPDS document.
        """
        media_type = media_type or OPDSCatalog.OPDS_1_TYPE
        self.http_client.queue_response(
            200,
            media_type,
            links = {AuthenticationDocument.AUTHENTICATION_DOCUMENT_REL: {'url': auth_url, 'rel': AuthenticationDocument.AUTHENTICATION_DOCUMENT_REL}}
        )

    def _auth_document(self, key=None):
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
                { "rel": "alternate", "href": "http://circmanager.org",
                  "type": "text/html" },
                {"rel": "logo", "href": "data:image/png;imagedata" },
                {"rel": "register", "href": "http://circmanager.org/new-account" },
                {"rel": "start", "href": "http://circmanager.org/feed/", "type": "application/atom+xml;profile=opds-catalog"},
                {"rel": "help", "href": "http://help.library.org/"},
                {"rel": "help", "href": "mailto:help@library.org"},
                {"rel": "http://librarysimplified.org/rel/designated-agent/copyright", "href": "mailto:dmca@library.org"},
            ],
            "service_area": { "US": "Kansas" },
            "collection_size": 100,
        }

        if key:
            auth_document['public_key'] = {
                "type": "RSA",
                "value": key.publickey().exportKey(),
            }
        return auth_document

    def test_register_get(self):

        # When there is no terms-of-service document, you can get a
        # document describing the authentication process but it's
        # empty.
        with self.app.test_request_context("/", method="GET"):
            response = self.controller.register()
            eq_(200, response.status_code)
            eq_('{}', response.data)

        # Set some terms of service.
        tos = "http://terms.com/service.html"
        ConfigurationSetting.sitewide(
            self._db, Configuration.REGISTRATION_TERMS_OF_SERVICE_URL
        ).value = tos

        # Now the document contains one link, to the terms of service
        # document.
        with self.app.test_request_context("/", method="GET"):
            response = self.controller.register()
            eq_(200, response.status_code)
            data = json.loads(response.data)
            [link] = data['links']
            eq_("terms-of-service", link["rel"])
            eq_(tos, link['href'])

    def test_register_fails_when_no_auth_document_url_provided(self):
        """Without the URL to an Authentication For OPDS document,
        the registration process can't begin.
        """
        with self.app.test_request_context("/", method="POST"):
            response = self.controller.register(do_get=self.http_client.do_get)

            eq_(NO_AUTH_URL, response)

    def test_register_fails_when_auth_document_url_times_out(self):
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            self.http_client.queue_response(
                RequestTimedOut("http://url", "sorry")
            )
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(TIMEOUT.uri, response.uri)
            eq_('Timeout retrieving auth document http://circmanager.org/authentication.opds', response.detail)

    def test_register_fails_on_non_200_code(self):
        """If the URL provided results in a status code other than
        200, the registration process can't begin.
        """
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form

            # This server isn't working.
            self.http_client.queue_response(500)
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(ERROR_RETRIEVING_DOCUMENT.uri, response.uri)
            eq_("Error retrieving auth document http://circmanager.org/authentication.opds", response.detail)

            # This server incorrectly requires authentication to
            # access the authentication document.
            self.http_client.queue_response(401)
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(ERROR_RETRIEVING_DOCUMENT.uri, response.uri)
            eq_("Error retrieving auth document http://circmanager.org/authentication.opds", response.detail)

            # This server doesn't have an authentication document
            # at the specified URL.
            self.http_client.queue_response(404)
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INTEGRATION_DOCUMENT_NOT_FOUND.uri, response.uri)
            eq_('No Authentication For OPDS document present at http://circmanager.org/authentication.opds', response.detail)

    def test_register_fails_on_non_authentication_document(self):
        # The request succeeds but returns something other than
        # an authentication document.
        self.http_client.queue_response(
            200, content="I am not an Authentication For OPDS document."
        )
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT, response)

    def test_register_fails_on_non_matching_id(self):
        # The request returns an authentication document but its `id`
        # doesn't match the final URL it was retrieved from.
        auth_document = self._auth_document()
        self.http_client.queue_response(
            200, content=json.dumps(auth_document),
            url="http://a-different-url/"
        )
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict([
                ("url", "http://a-different-url/"),
                ("contact", "mailto:me@library.org"),
            ])
            response = self.controller.register(do_get=self.http_client.do_get)

            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("The OPDS authentication document's id (http://circmanager.org/authentication.opds) doesn't match its url (http://a-different-url/).",
                response.detail)

    def test_register_fails_on_missing_title(self):
        # The request returns an authentication document but it's missing
        # a title.
        auth_document = self._auth_document()
        del auth_document['title']
        self.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document['id']
        )
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("The OPDS authentication document is missing a title.",
                response.detail)

    def test_register_fails_on_no_start_link(self):
        # The request returns an authentication document but it's missing
        # a link to an OPDS feed.
        auth_document = self._auth_document()
        for link in list(auth_document['links']):
            if link['rel'] == 'start':
                auth_document['links'].remove(link)
        self.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document['id']
        )
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("The OPDS authentication document is missing a 'start' link to the root OPDS feed.",
                response.detail)

    def test_register_fails_on_start_link_not_found(self):
        # The request returns an authentication document but an attempt
        # to retrieve the corresponding OPDS feed yields a 404.
        auth_document = self._auth_document()
        self.http_client.queue_response(
            200, content=json.dumps(auth_document),
            url=auth_document['id']
        )
        self.http_client.queue_response(404)
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INTEGRATION_DOCUMENT_NOT_FOUND.uri, response.uri)
            eq_("No OPDS root document present at http://circmanager.org/feed/",
                response.detail)

    def test_register_fails_on_start_link_timeout(self):
        # The request returns an authentication document but an attempt
        # to retrieve the corresponding OPDS feed times out.
        auth_document = self._auth_document()
        self.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document['id']
        )
        self.http_client.queue_response(RequestTimedOut("http://url", "sorry"))
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(TIMEOUT.uri, response.uri)
            eq_("Timeout retrieving OPDS root document at http://circmanager.org/feed/",
                response.detail)

    def test_register_fails_on_start_link_error(self):
        # The request returns an authentication document but an attempt
        # to retrieve the corresponding OPDS feed gives a server-side error.
        auth_document = self._auth_document()
        self.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document['id']
        )
        self.http_client.queue_response(500)
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(ERROR_RETRIEVING_DOCUMENT.uri, response.uri)
            eq_("Error retrieving OPDS root document at http://circmanager.org/feed/", response.detail)

    def test_register_fails_on_start_link_not_opds_feed(self):
        """The request returns an authentication document but an attempt
        to retrieve the corresponding OPDS feed gives a server-side error.
        """
        auth_document = self._auth_document()
        self.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document['id']
        )

        # The start link returns a 200 response code but the wrong
        # Content-Type.
        self.http_client.queue_response(200, "text/html")
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("Supposed root document at http://circmanager.org/feed/ is not an OPDS document", response.detail)

    def test_register_fails_if_start_link_does_not_link_back_to_auth_document(self):
        auth_document = self._auth_document()
        self.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document['id']
        )

        # The start link returns a 200 response code and the right
        # Content-Type, but there is no Link header and the body is no
        # help.
        self.http_client.queue_response(
            200, OPDSCatalog.OPDS_TYPE, content='{}'
        )
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("OPDS root document at http://circmanager.org/feed/ does not link back to authentication document http://circmanager.org/authentication.opds", response.detail)

    def test_register_fails_on_broken_logo_link(self):
        """The request returns a valid authentication document
        that links to a broken logo image.
        """
        auth_document = self._auth_document()
        for link in auth_document['links']:
            if link['rel'] == 'logo':
                link['href'] = "http://example.com/broken-logo.png"
                break
        # Auth document request succeeds.
        self.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document['id']
        )

        # OPDS feed request succeeds.
        self.queue_opds_success()

        # Image request fails.
        self.http_client.queue_response(500)

        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("Could not read logo image http://example.com/broken-logo.png",
                response.detail)

    def test_register_fails_on_unknown_service_area(self):
        """The auth document is valid but the registry doesn't recognize the
        library's service area.
        """
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            auth_document = self._auth_document()
            auth_document['service_area'] = {"US": ["Somewhere"]}
            self.http_client.queue_response(200, content=json.dumps(auth_document), url=auth_document['id'])
            self.queue_opds_success()
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("The following service area was unknown: {\"US\": [\"Somewhere\"]}.", response.detail)

    def test_register_fails_on_ambiguous_service_area(self):

        # Create a situation (which shouldn't exist in real life)
        # where there are two places with the same name and the same
        # .parent.
        self.new_york_city.parent = self.crude_us
        self.manhattan_ks.parent = self.crude_us

        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            auth_document = self._auth_document()
            auth_document['service_area'] = {"US": ["Manhattan"]}
            self.http_client.queue_response(
                200, content=json.dumps(auth_document),
                url=auth_document['id']
            )
            self.queue_opds_success()
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("The following service area was ambiguous: {\"US\": [\"Manhattan\"]}.", response.detail)

    def test_register_fails_on_401_with_no_authentication_document(self):
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            auth_document = self._auth_document()
            self.http_client.queue_response(
                200, content=json.dumps(auth_document), url=auth_document['id']
            )
            self.http_client.queue_response(401)
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("401 response at http://circmanager.org/feed/ did not yield an Authentication For OPDS document", response.detail)

    def test_register_fails_on_401_if_authentication_document_ids_do_not_match(self):
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            auth_document = self._auth_document()
            self.http_client.queue_response(
                200, content=json.dumps(auth_document),
                url=auth_document['id']
            )
            auth_document['id'] = "http://some-other-id/"
            self.http_client.queue_response(
                401, AuthenticationDocument.MEDIA_TYPE,
                content=json.dumps(auth_document),
                url=auth_document['id']
            )

            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("Authentication For OPDS document guarding http://circmanager.org/feed/ does not match the one at http://circmanager.org/authentication.opds", response.detail)

    def test_register_succeeds_on_401_if_authentication_document_ids_match(self):
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            auth_document = self._auth_document()
            self.http_client.queue_response(
                200, content=json.dumps(auth_document),
                url=auth_document['id']
            )
            self.http_client.queue_response(
                401, AuthenticationDocument.MEDIA_TYPE,
                content=json.dumps(auth_document),
                url=auth_document['id']
            )

            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(201, response.status_code)

    # NOTE: This is commented out until we can say that registration
    # requires providing a contact email and expect every new library
    # to be on a circulation manager that can meet this requirement.
    #
    # def test_register_fails_on_no_contact_email(self):
    #     with self.app.test_request_context("/", method="POST"):
    #         flask.request.form = ImmutableMultiDict([
    #             ("url", "http://circmanager.org/authentication.opds"),
    #         ])
    #         response = self.controller.register(do_get=self.http_client.do_get)
    #         eq_("Invalid or missing configuration contact email address",
    #             response.title)

    #         flask.request.form = ImmutableMultiDict([
    #             ("url", "http://circmanager.org/authentication.opds"),
    #             ("contact", "http://contact-us/")
    #         ])
    #         response = self.controller.register(do_get=self.http_client.do_get)
    #         eq_("Invalid or missing configuration contact email address",
    #             response.title)

    def test_register_fails_on_missing_email_in_authentication_document(self):

        for (rel, error) in (
                ("http://librarysimplified.org/rel/designated-agent/copyright",
                 "Invalid or missing copyright designated agent email address"),
                ("help", "Invalid or missing patron support email address")
        ):
            # Start with a valid document.
            auth_document = self._auth_document()

            # Remove the crucial link.
            auth_document['links'] = filter(
            lambda x: x['rel'] != rel or not x['href'].startswith("mailto:"),
                auth_document['links']
            )

            def _request_fails():
                self.http_client.queue_response(
                    200, content=json.dumps(auth_document),
                    url=auth_document['id']
                )
                with self.app.test_request_context("/", method="POST"):
                    flask.request.form = self.registration_form
                    response = self.controller.register(do_get=self.http_client.do_get)
                    eq_(error, response.title)
            _request_fails()

            # Now add the link back but as an http: link.
            auth_document['links'].append(
                dict(rel=rel, href="http://not-an-email/")
            )
            _request_fails()

    def test_registration_fails_if_email_server_fails(self):
        """Even if everything looks good, registration can fail if
        the library registry can't send out the validation emails.
        """
        # Simulate an SMTP server that won't accept email for
        # whatever reason.
        class NonfunctionalEmailer(MockEmailer):
            def send(self, *args, **kwargs):
                raise SMTPException("SMTP server is broken")
        self.controller.emailer = NonfunctionalEmailer()

        # Pretend we are a library with a valid authentication document.
        auth_document = self._auth_document(None)
        self.http_client.queue_response(
            200, content=json.dumps(auth_document),
            url=auth_document['id']
        )
        self.queue_opds_success()

        auth_url = "http://circmanager.org/authentication.opds"
        # Send a registration request to the registry.
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict([
                ("url", auth_document['id']),
                ("contact", "mailto:me@library.org"),
            ])
            response = self.controller.register(do_get=self.http_client.do_get)

        # We get back a ProblemDetail the first time
        # we got a problem sending an email. In this case, it was
        # trying to contact the library's 'help' address included in the
        # library's authentication document.
        eq_(INTEGRATION_ERROR.uri, response.uri)
        eq_("SMTP error while sending email to mailto:help@library.org",
            response.detail)

    def test_register_success(self):
        opds_directory = "application/opds+json;profile=https://librarysimplified.org/rel/profile/directory"

        # Pretend we are a library with a valid authentication document.
        key = RSA.generate(1024)
        auth_document = self._auth_document(key)
        self.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document['id']
        )
        self.queue_opds_success()

        auth_url = "http://circmanager.org/authentication.opds"
        opds_url = "http://circmanager.org/feed/"

        # Send a registration request to the registry.
        random.seed(42)
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict([
                ("url", auth_url),
                ("contact", "mailto:me@library.org"),
            ])
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(201, response.status_code)
            eq_(opds_directory, response.headers.get("Content-Type"))

            # The library has been created. Information from its
            # authentication document has been added to the database.
            library = get_one(self._db, Library, opds_url=opds_url)
            assert library != None
            eq_("A Library", library.name)
            eq_("Description", library.description)
            eq_("http://circmanager.org", library.web_url)
            eq_("data:image/png;imagedata", library.logo)

            # The client didn't specify a stage, so the server acted
            # like the client asked to be put into production.
            eq_(Library.PRODUCTION_STAGE, library.library_stage)

            eq_(True, library.anonymous_access)
            eq_(True, library.online_registration)

            [collection_summary] = library.collections
            eq_(None, collection_summary.language)
            eq_(100, collection_summary.size)
            [service_area] = library.service_areas
            eq_(self.kansas_state.id, service_area.place_id)

            # To get this information, a request was made to the
            # circulation manager's Authentication For OPDS document.
            # A follow-up request was made to the feed mentioned in that
            # document.
            #
            eq_(["http://circmanager.org/authentication.opds",
                 "http://circmanager.org/feed/"
            ],
                self.http_client.requests)

            # And the document we queued up was fed into the library
            # registry.
            catalog = json.loads(response.data)
            eq_("A Library", catalog['metadata']['title'])
            eq_('Description', catalog['metadata']['description'])

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
            eq_('QAHFTR', library.short_name)
            eq_(48, len(library.shared_secret))

            eq_(library.short_name, catalog["metadata"]["short_name"])
            # The registry encrypted the secret with the public key, and
            # it can be decrypted with the private key.
            encryptor = PKCS1_OAEP.new(key)
            encrypted_secret = base64.b64decode(catalog["metadata"]["shared_secret"])
            eq_(library.shared_secret, encryptor.decrypt(encrypted_secret))

        old_secret = library.shared_secret
        self.http_client.requests = []

        # Hyperlink objects were created for the three email addresses
        # associated with the library.
        help_link, copyright_agent_link, integration_contact_link = sorted(
            library.hyperlinks, key=lambda x: x.rel
        )
        eq_("help", help_link.rel)
        eq_("mailto:help@library.org", help_link.href)
        eq_(Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL, copyright_agent_link.rel)
        eq_("mailto:dmca@library.org", copyright_agent_link.href)
        eq_(Hyperlink.INTEGRATION_CONTACT_REL, integration_contact_link.rel)
        eq_("mailto:me@library.org", integration_contact_link.href)

        # A confirmation email was sent out for each of those addresses.
        sent = sorted(self.controller.emailer.sent_out, key=lambda x: x[1])
        for email in sent:
            eq_(Emailer.ADDRESS_NEEDS_CONFIRMATION, email[0])
        destinations = [x[1] for x in sent]
        eq_(["dmca@library.org", "help@library.org", "me@library.org"],
            destinations)
        self.controller.emailer.sent_out = []

        # The document sent by the library registry to the library
        # includes status information about the library's integration
        # contact address -- information that wouldn't be made
        # available to the public.
        [link] = [x for x in catalog['links'] if
                  x.get('rel') == Hyperlink.INTEGRATION_CONTACT_REL]
        eq_("mailto:me@library.org", link['href'])
        eq_(Validation.IN_PROGRESS,
            link['properties'][Validation.STATUS_PROPERTY])

        # Later, the library's information changes.
        auth_document = {
            "id": auth_url,
            "name": "A Library",
            "service_description": "New and improved",
            "links": [
                {"rel": "logo", "href": "/logo.png", "type": "image/png" },
                {"rel": "start", "href": "http://circmanager.org/feed/", "type": "application/atom+xml;profile=opds-catalog"},
                {"rel": "help", "href": "mailto:new-help@library.org"},
                {"rel": "http://librarysimplified.org/rel/designated-agent/copyright", "href": "mailto:me@library.org"},

            ],
            "service_area": { "US": "Connecticut" },
        }
        self.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document['id']
        )
        self.queue_opds_success()

        # We have a new logo as well.
        image_data = '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x01\x03\x00\x00\x00%\xdbV\xca\x00\x00\x00\x06PLTE\xffM\x00\x01\x01\x01\x8e\x1e\xe5\x1b\x00\x00\x00\x01tRNS\xcc\xd24V\xfd\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'
        self.http_client.queue_response(200, content=image_data, media_type="image/png")

        # So the library re-registers itself, and gets an updated
        # registry entry.
        #
        # This time, the library explicitly specifies which stage it
        # wants to be in.
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict([
                ("url", auth_url),
                ("contact", "mailto:me@library.org"),
                ("stage", Library.TESTING_STAGE)
            ])

            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(200, response.status_code)
            eq_(opds_directory, response.headers.get("Content-Type"))

            # The data sent in the response includes the library's new
            # data.
            catalog = json.loads(response.data)
            eq_("A Library", catalog['metadata']['title'])
            eq_('New and improved', catalog['metadata']['description'])

            # The library's new data is also in the database.
            library = get_one(self._db, Library, opds_url=opds_url)
            assert library != None
            eq_("A Library", library.name)
            eq_("New and improved", library.description)
            eq_(None, library.web_url)
            eq_("data:image/png;base64,%s" % base64.b64encode(image_data), library.logo)
            # The library's library_stage has been updated to reflect
            # the 'stage' method passed in from the client.
            eq_(Library.TESTING_STAGE, library.library_stage)

            # There are still three Hyperlinks associated with the
            # library.
            help_link_2, copyright_agent_link_2, integration_contact_link_2 = sorted(
                library.hyperlinks, key=lambda x: x.rel
            )

            # The Hyperlink objects are the same as before.
            eq_(help_link_2, help_link)
            eq_(copyright_agent_link_2, copyright_agent_link)
            eq_(integration_contact_link_2, integration_contact_link)

            # But two of the hrefs have been updated to reflect the new
            # authentication document.
            eq_("help", help_link.rel)
            eq_("mailto:new-help@library.org", help_link.href)
            eq_(Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL, copyright_agent_link.rel)
            eq_("mailto:me@library.org", copyright_agent_link.href)

            # The link that hasn't changed is unaffected.
            eq_(Hyperlink.INTEGRATION_CONTACT_REL, integration_contact_link.rel)
            eq_("mailto:me@library.org", integration_contact_link.href)

            # Two emails were sent out -- one asking for confirmation
            # of new-help@library.org, and one announcing the new role
            # for me@library.org (which already has an outstanding
            # confirmation request) as designated copyright agent.
            new_dmca, new_help = sorted(
                [(x[1], x[0]) for x in self.controller.emailer.sent_out]
            )
            eq_(("me@library.org", Emailer.ADDRESS_DESIGNATED), new_dmca)
            eq_(("new-help@library.org", Emailer.ADDRESS_NEEDS_CONFIRMATION),
                new_help)

            # Commit to update library.service_areas.
            self._db.commit()

            # The library's service areas have been updated.
            [service_area] = library.service_areas
            eq_(self.connecticut_state.id, service_area.place_id)

            # In addition to making the request to get the
            # Authentication For OPDS document, and the request to
            # get the root OPDS feed, the registry made a
            # follow-up request to download the library's logo.
            eq_(["http://circmanager.org/authentication.opds",
                 "http://circmanager.org/feed/",
                 "http://circmanager.org/logo.png"], self.http_client.requests)


        # If we include the old secret in a request and also set
        # reset_shared_secret, the registry will generate a new
        # secret.
        form_args_no_reset = ImmutableMultiDict([
            ("url", "http://circmanager.org/authentication.opds"),
            ("contact", "mailto:me@library.org")
        ])
        form_args_with_reset = ImmutableMultiDict(
            form_args_no_reset.items() + [
                ("reset_shared_secret", "y")
            ]
        )
        with self.app.test_request_context("/", headers={"Authorization": "Bearer %s" % old_secret}, method="POST"):
            flask.request.form = form_args_with_reset
            key = RSA.generate(1024)
            auth_document = self._auth_document(key)
            self.http_client.queue_response(
                200, content=json.dumps(auth_document), url=auth_document['id']
            )
            self.queue_opds_success()

            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(200, response.status_code)
            catalog = json.loads(response.data)
            assert library.shared_secret != old_secret

            # The registry encrypted the new secret with the public key, and
            # it can be decrypted with the private key.
            encryptor = PKCS1_OAEP.new(key)
            encrypted_secret = base64.b64decode(catalog["metadata"]["shared_secret"])
            eq_(library.shared_secret, encryptor.decrypt(encrypted_secret))

        old_secret = library.shared_secret

        # If we include an incorrect secret, or we don't ask for the
        # secret to be reset, the secret doesn't change.
        for secret, form in (
            ("notthesecret", form_args_with_reset),
            (library.shared_secret, form_args_no_reset)
        ):
            with self.app.test_request_context("/", headers={"Authorization": "Bearer %s" % secret}):
                flask.request.form = form

                key = RSA.generate(1024)
                auth_document = self._auth_document(key)
                self.http_client.queue_response(
                    200, content=json.dumps(auth_document)
                )
                self.queue_opds_success()

                response = self.controller.register(
                    do_get=self.http_client.do_get
                )

                eq_(200, response.status_code)
                eq_(old_secret, library.shared_secret)

    def test_register_with_secret_changes_authentication_url_and_opds_url(self):
        # This Library was created previously with a certain shared
        # secret, at a URL that's no longer valid.
        secret = "it's a secret"
        library = self._library()
        library.authentication_url = "http://old-url/authentication_document"
        library.opds_url = "http://old-url/opds"
        library.shared_secret = secret

        # We're going to register a library at an apparently new URL,
        # but since we're providing the shared secret for an existing
        # Library, the registry will know to modify that Library instead
        # of creating a new one.
        auth_document = self._auth_document()
        new_auth_url = auth_document['id']
        [new_opds_url] = [
            x['href'] for x in auth_document['links']
            if x['rel']=='start'
        ]
        self.http_client.queue_response(
            200, content=json.dumps(auth_document), url=new_auth_url
        )
        self.queue_opds_success()
        with self.app.test_request_context("/", method="POST"):
            flask.request.headers = {
                "Authorization": "Bearer %s" % secret
            }
            flask.request.form = ImmutableMultiDict([
                ("url", new_auth_url),
            ])
            response = self.controller.register(do_get=self.http_client.do_get)
            # No new library was created.
            eq_(200, response.status_code)

        # The library's authentication_url and opds_url have been modified.
        eq_(new_auth_url, library.authentication_url)
        eq_(new_opds_url, library.opds_url)


class TestValidationController(ControllerTest):

    def test_html_response(self):
        """Test the generation of a simple HTML-based HTTP response."""
        controller = ValidationController(self.library_registry)
        response = controller.html_response(999, "a message")
        eq_(999, response.status_code)
        eq_("text/html", response.headers['Content-Type'])
        eq_(controller.MESSAGE_TEMPLATE % dict(message="a message"),
            response.data)

    def test_validate(self):
        class Mock(ValidationController):
            def html_response(self, status_code, message):
                return (status_code, message)

        controller = Mock(self.library_registry)
        def assert_response(resource_id, secret, status_code, message):
            """Invoke the validate() method with the given secret
            and verify that html_response is called with the given
            status_code and message.
            """
            result = controller.confirm(resource_id, secret)
            eq_((status_code, message), result)

        # This library has three links: two that are in the middle of
        # the validation process and one that has not started the
        # validation process.
        library = self._library()

        link1, ignore = library.set_hyperlink("rel", "mailto:1@library.org")
        needs_validation = link1.resource
        needs_validation.restart_validation()
        secret = needs_validation.validation.secret

        link2, ignore = library.set_hyperlink("rel2", "mailto:2@library.org")
        needs_validation_2 = link2.resource
        needs_validation_2.restart_validation()
        secret2 = needs_validation_2.validation.secret

        link3, ignore = library.set_hyperlink("rel2", "mailto:3@library.org")
        not_started = link3.resource

        # Simple tests for missing fields or failed lookups.
        assert_response(
            needs_validation.id, "", 404, "No confirmation code provided"
        )
        assert_response(None, "a code", 404, "No resource ID provided")
        assert_response(-20, secret, 404, "No such resource")

        # Secret does not exist.
        assert_response(
            needs_validation.id, "nosuchcode", 404,
            "Confirmation code 'nosuchcode' not found"
        )

        # Secret exists but is associated with a different Resource.
        assert_response(needs_validation.id, secret2, 404,
                        "Confirmation code %r not found" % secret2)

        # Secret exists but is not associated with any Resource (this
        # shouldn't happen).
        needs_validation_2.validation.resource = None
        assert_response(needs_validation.id, secret2, 404,
                        "Confirmation code %r not found" % secret2)

        # Secret matches resource but validation has expired.
        needs_validation.validation.started_at = (
            datetime.datetime.now() - datetime.timedelta(days=7)
        )
        assert_response(
            needs_validation.id, secret, 400,
            "Confirmation code %r has expired. Re-register to get another code." % secret
        )

        # Success.
        needs_validation.restart_validation()
        secret = needs_validation.validation.secret
        assert_response(
            needs_validation.id, secret, 200,
            "You successfully confirmed mailto:1@library.org."
        )

        # A Resource can't be validated twice.
        assert_response(
            needs_validation.id, secret, 200,
            "This URI has already been validated."
        )

class TestCoverageController(ControllerTest):

    def setup(self):
        super(TestCoverageController, self).setup()
        self.controller = CoverageController(self.library_registry)

    def parse_to(
            self, coverage, places=[], ambiguous=None,
            unknown=None, to_json=True
    ):
        # Make a request to the coverage controller to turn a coverage
        # object into GeoJSON. Verify that the Places in
        # `places` are represented in the coverage object
        # and that the 'ambiguous' and 'unknown' extensions
        # are also as expected.
        if to_json:
            coverage = json.dumps(coverage)
        with self.app.test_request_context(
            "/?coverage=%s" % coverage, method="POST"
        ):
            response = self.controller.lookup()

        # The response is always GeoJSON.
        eq_("application/geo+json", response.headers['Content-Type'])
        geojson = json.loads(response.data)

        # Unknown or ambiguous places will be mentioned in
        # these extra fields.
        actual_unknown = geojson.pop('unknown', None)
        eq_(actual_unknown, unknown)
        actual_ambiguous = geojson.pop('ambiguous', None)
        eq_(ambiguous, actual_ambiguous)

        # Without those extra fields, the GeoJSON document should be
        # identical to the one we get by calling Place.to_geojson
        # on the expected places.
        expect_geojson = Place.to_geojson(self._db, *places)
        eq_(expect_geojson, geojson)

    def test_lookup(self):
        # Set up a default nation to make it easier to test a variety
        # of coverage area types.
        ConfigurationSetting.sitewide(
            self._db, Configuration.DEFAULT_NATION_ABBREVIATION
        ).value = "US"

        # Set up some places.
        kansas = self.kansas_state
        massachussets = self.massachussets_state
        boston = self.boston_ma

        # Parse some strings to GeoJSON objects.
        self.parse_to("Boston, MA", [boston], to_json=False)
        self.parse_to("Boston, MA", [boston], to_json=True)
        self.parse_to("Massachussets", [massachussets])
        self.parse_to(["Massachussets", "Kansas"], [massachussets, kansas])
        self.parse_to({"US": "Kansas"}, [kansas])
        self.parse_to({"US": ["Massachussets", "Kansas"]},
                      [massachussets, kansas])
        self.parse_to(["KS", "UT"], [kansas], unknown={"US": ["UT"]})

        # Creating two states with the same name is the simplest way
        # to create an ambiguity problem.
        massachussets.external_name="Kansas"
        self.parse_to("Kansas", [], ambiguous={"US": ["Kansas"]})

    def test_library_eligibility_and_focus(self):
        # focus_for_library() and eligibility_for_library() represent
        # a library's service area as GeoJSON.

        # We don't use self.nypl here because we want to set more
        # realistic service and focus areas.
        nypl = self._library("NYPL")

        # New York State is the eligibility area for NYPL.
        get_one_or_create(
            self._db, ServiceArea, library=nypl,
            place=self.new_york_state, type=ServiceArea.ELIGIBILITY
        )

        # New York City is the focus area.
        get_one_or_create(
            self._db, ServiceArea, library=nypl,
            place=self.new_york_city, type=ServiceArea.FOCUS
        )

        with self.request_context_with_library("/", library=nypl):
            focus = self.app.library_registry.coverage_controller.focus_for_library()
            eligibility = self.app.library_registry.coverage_controller.eligibility_for_library()

            # In both cases we got a GeoJSON document
            for response in (focus, eligibility):
                eq_(200, response.status_code)
                eq_("application/geo+json", response.headers['Content-Type'])

            # The GeoJSON documents are the ones we'd expect from turning
            # the corresponding service areas into GeoJSON.
            focus = json.loads(focus.data)
            eq_(Place.to_geojson(self._db, self.new_york_city), focus)

            eligibility = json.loads(eligibility.data)
            eq_(Place.to_geojson(self._db, self.new_york_state), eligibility)
