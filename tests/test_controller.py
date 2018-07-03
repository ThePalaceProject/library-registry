from nose.tools import (
    eq_,
    set_trace,
)
import os
import json
import base64

from controller import (
    LibraryRegistry,
    LibraryRegistryController,
)

import flask
from flask import Response
from werkzeug import ImmutableMultiDict
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP

from . import DatabaseTest
from testing import DummyHTTPClient
from util.problem_detail import ProblemDetail

from authentication_document import AuthenticationDocument
from opds import OPDSCatalog
from model import (
    get_one,
    get_one_or_create,
    ConfigurationSetting,
    ExternalIntegration,
    Hyperlink,
    Library,
)
from util.http import RequestTimedOut
from problem_details import *
from config import Configuration
from testing import DummyHTTPResponse

class TestLibraryRegistry(LibraryRegistry):
    pass


class ControllerTest(DatabaseTest):
    def setup(self):
        super(ControllerTest, self).setup()
        os.environ['AUTOINITIALIZE'] = "False"
        from app import app
        del os.environ['AUTOINITIALIZE']
        self.app = app

        # Create some places and libraries.
        nypl = self.nypl
        ct_state = self.connecticut_state_library
        ks_state = self.kansas_state_library

        nyc = self.new_york_city
        boston = self.boston_ma
        manhattan_ks = self.manhattan_ks
        us = self.crude_us

        # Configure a basic vendor id service.
        integration, ignore = get_one_or_create(
            self._db, ExternalIntegration,
            protocol=ExternalIntegration.ADOBE_VENDOR_ID,
            goal=ExternalIntegration.DRM_GOAL,
        )
        integration.setting(Configuration.ADOBE_VENDOR_ID).value = "VENDORID"
        
        self.library_registry = TestLibraryRegistry(self._db, testing=True)
        self.app.library_registry = self.library_registry
        self.controller = LibraryRegistryController(self.library_registry)
        self.http_client = DummyHTTPClient()

        # A registration form that's valid for most of the tests 
        # in this module.
        self.registration_form = ImmutableMultiDict([
            ("url", "http://circmanager.org/authentication.opds"),
            ("contact", "mailto:integrationproblems@library.org"),
        ])

class TestLibraryRegistryController(ControllerTest):

    def test_nearby(self):
        with self.app.test_request_context("/"):
            response = self.controller.nearby("65.88.88.124")
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
            [register_link, search_link, self_link] = sorted(
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

            eq_("VENDORID", catalog["metadata"]["adobe_vendor_id"])

    def test_nearby_qa(self):
        # The library we used in the previous test has stage=LIVE.
        # If we switch to looking for libraries with stage=APPROVED,
        # we won't find anything.
        with self.app.test_request_context("/"):
            response = self.controller.nearby("65.88.88.124", live=False)
            catalogs = json.loads(response.data)
            eq_([], catalogs['catalogs'])

        # If we move the LIVE library to APPROVED, it shows up in
        # the feed.
        self.connecticut_state_library.stage = Library.APPROVED
        with self.app.test_request_context("/"):
            response = self.controller.nearby("65.88.88.124", live=False)
            catalogs = json.loads(response.data)
            [catalog] = catalogs['catalogs']
            assert("", catalog['metadata']['title'])

            # Some of the links are the same as in the production feed;
            # others are different.
            url_for = self.app.library_registry.url_for
            [register_link, search_link, self_link] = sorted(
                catalogs['links'], key=lambda x: x['rel']
            )

            # The 'register' link is the same as in the main feed.
            eq_(url_for("register"), register_link["href"])
            eq_("register", register_link["rel"])

            # This is a QA feed, and the 'search' and 'self' links
            # will give results from the QA feed.
            eq_(url_for("nearby_qa"), self_link['href'])
            eq_("self", self_link['rel'])

            eq_(url_for("search_qa"), search_link['href'])
            eq_("search", search_link['rel'])

    def test_nearby_no_ip_address(self):
        with self.app.test_request_context("/"):
            response = self.controller.nearby(None)
            assert isinstance(response, Response)
            eq_("200 OK", response.status)
            eq_(OPDSCatalog.OPDS_TYPE, response.headers['Content-Type'])
            catalogs = json.loads(response.data)

            # We found no nearby libraries, because we had no IP address to
            # start with.
            eq_([], catalogs['catalogs'])

    def test_nearby_no_libraries(self):
        with self.app.test_request_context("/"):
            response = self.controller.nearby("8.8.8.8") # California
            assert isinstance(response, Response)
            eq_("200 OK", response.status)
            eq_(OPDSCatalog.OPDS_TYPE, response.headers['Content-Type'])
            catalog = json.loads(response.data)

            # We found no nearby libraries, because we were too far away
            # from them.
            eq_([], catalog['catalogs'])
            
    def test_search_form(self):
        with self.app.test_request_context("/"):
            response = self.controller.search()
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
            response = self.controller.search(live=False)
            eq_("200 OK", response.status)

            expect_url = self.library_registry.url_for("search_qa")
            expect_url_tag = '<Url type="application/atom+xml;profile=opds-catalog" template="%s?q={searchTerms}"/>' % expect_url
            assert expect_url_tag in response.data
            
    def test_search(self):
        with self.app.test_request_context("/?q=manhattan"):
            response = self.controller.search("65.88.88.124")
            eq_("200 OK", response.status)
            eq_(OPDSCatalog.OPDS_TYPE, response.headers['Content-Type'])
            catalog = json.loads(response.data)
            # We found the two matching results.
            [nypl, ks] = catalog['catalogs']
            eq_("NYPL", nypl['metadata']['title'])
            eq_("0 km.", nypl['metadata']['distance'])

            eq_("Kansas State Library", ks['metadata']['title'])
            eq_("1922 km.", ks['metadata']['distance'])

            [register_link, search_link, self_link] = sorted(
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

            eq_("VENDORID", catalog["metadata"]["adobe_vendor_id"])

    def test_search_qa(self):
        # As we saw in the previous test, this search picks up two
        # libraries when we run it looking for LIVE libraries. But
        # since we're only searching for libraries in the APPROVED
        # stage, we don't find anything.
        with self.app.test_request_context("/?q=manhattan"):
            response = self.controller.search("65.88.88.124", live=False)
            catalog = json.loads(response.data)
            eq_([], catalog['catalogs'])

        # If we move one of the libraries back into the APPROVED
        # stage, we find it.
        self.kansas_state_library.stage = Library.APPROVED
        with self.app.test_request_context("/?q=manhattan"):
            response = self.controller.search("65.88.88.124", live=False)
            catalog = json.loads(response.data)
            [catalog] = catalog['catalogs']
            eq_('Kansas State Library', catalog['metadata']['title'])

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
        tos = "Some terms"
        ConfigurationSetting.sitewide(
            self._db, Configuration.REGISTRATION_TERMS_OF_SERVICE_TEXT
        ).value = tos

        # Now the document 'links' to the terms of service via a data:
        # URI.
        with self.app.test_request_context("/", method="GET"):
            response = self.controller.register()
            eq_(200, response.status_code)
            data = json.loads(response.data)
            [link] = data['links']
            eq_("terms-of-service", link["rel"])
            eq_("data:text/html;%s" % base64.encodestring(tos),
                link['href'])

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
        """The request succeeds but returns something other than
        an authentication document.
        """
        self.http_client.queue_response(
            200, content="I am not an Authentication For OPDS document."
        )
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT, response)

    def test_register_fails_on_non_matching_id(self):
        """The request returns an authentication document but its `id`
        doesn't match the URL it was retrieved from.
        """
        auth_document = self._auth_document()
        self.http_client.queue_response(200, content=json.dumps(auth_document))
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
        """The request returns an authentication document but it's missing
        a title.
        """
        auth_document = self._auth_document()
        del auth_document['title']
        self.http_client.queue_response(200, content=json.dumps(auth_document))
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("The OPDS authentication document is missing a title.",
                response.detail)

    def test_register_fails_on_no_start_link(self):
        """The request returns an authentication document but it's missing
        a link to an OPDS feed.
        """
        auth_document = self._auth_document()
        for link in list(auth_document['links']):
            if link['rel'] == 'start':
                auth_document['links'].remove(link)
        self.http_client.queue_response(200, content=json.dumps(auth_document))
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("The OPDS authentication document is missing a 'start' link to the root OPDS feed.",
                response.detail)

    def test_register_fails_on_start_link_not_found(self):
        """The request returns an authentication document but an attempt
        to retrieve the corresponding OPDS feed yields a 404.
        """
        auth_document = self._auth_document()
        self.http_client.queue_response(200, content=json.dumps(auth_document))
        self.http_client.queue_response(404)
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INTEGRATION_DOCUMENT_NOT_FOUND.uri, response.uri)
            eq_("No OPDS root document present at http://circmanager.org/feed/",
                response.detail)

    def test_register_fails_on_start_link_timeout(self):
        """The request returns an authentication document but an attempt
        to retrieve the corresponding OPDS feed times out.
        """
        auth_document = self._auth_document()
        self.http_client.queue_response(200, content=json.dumps(auth_document))
        self.http_client.queue_response(RequestTimedOut("http://url", "sorry"))
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(TIMEOUT.uri, response.uri)
            eq_("Timeout retrieving OPDS root document at http://circmanager.org/feed/", 
                response.detail)

    def test_register_fails_on_start_link_error(self):
        """The request returns an authentication document but an attempt
        to retrieve the corresponding OPDS feed gives a server-side error.
        """
        auth_document = self._auth_document()
        self.http_client.queue_response(200, content=json.dumps(auth_document))
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
        self.http_client.queue_response(200, content=json.dumps(auth_document))

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
        self.http_client.queue_response(200, content=json.dumps(auth_document))

        # The start link returns a 200 response code and the right
        # Content-Type, but there is no Link header and the body is no
        # help.
        self.http_client.queue_response(200, OPDSCatalog.OPDS_TYPE, content='{}')
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
        self.http_client.queue_response(200, content=json.dumps(auth_document))

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
            self.http_client.queue_response(200, content=json.dumps(auth_document))
            self.queue_opds_success()
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("The following service area was unknown: {\"US\": [\"Somewhere\"]}.", response.detail)

    def test_register_fails_on_ambiguous_service_area(self):
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            auth_document = self._auth_document()
            auth_document['service_area'] = {"US": ["Manhattan"]}
            self.http_client.queue_response(200, content=json.dumps(auth_document))
            self.queue_opds_success()
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("The following service area was ambiguous: {\"US\": [\"Manhattan\"]}.", response.detail)

    def test_register_fails_on_401_with_no_authentication_document(self):
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            auth_document = self._auth_document()
            self.http_client.queue_response(200, content=json.dumps(auth_document))
            self.http_client.queue_response(401)
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("401 response at http://circmanager.org/feed/ did not yield an Authentication For OPDS document", response.detail)

    def test_register_fails_on_401_if_authentication_document_ids_do_not_match(self):
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            auth_document = self._auth_document()
            self.http_client.queue_response(200, content=json.dumps(auth_document))
            auth_document['id'] = "http://some-other-id/"
            self.http_client.queue_response(
                401, AuthenticationDocument.MEDIA_TYPE, content=json.dumps(auth_document
            ))

            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(INVALID_INTEGRATION_DOCUMENT.uri, response.uri)
            eq_("Authentication For OPDS document guarding http://circmanager.org/feed/ does not match the one at http://circmanager.org/authentication.opds", response.detail)

    def test_register_succeeds_on_401_if_authentication_document_ids_match(self):
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = self.registration_form
            auth_document = self._auth_document()
            self.http_client.queue_response(200, content=json.dumps(auth_document))
            self.http_client.queue_response(
                401, AuthenticationDocument.MEDIA_TYPE, content=json.dumps(auth_document
            ))

            response = self.controller.register(do_get=self.http_client.do_get)
            eq_(201, response.status_code)

    def test_register_fails_on_no_contact_email(self):
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict([
                ("url", "http://circmanager.org/authentication.opds"),
            ])
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_("No valid integration contact address", response.title)

            flask.request.form = ImmutableMultiDict([
                ("url", "http://circmanager.org/authentication.opds"),
                ("contact", "http://contact-us/")
            ])
            response = self.controller.register(do_get=self.http_client.do_get)
            eq_("No valid integration contact address", response.title)

    def test_register_fails_on_missing_email_in_authentication_document(self):

        for (rel, error) in (
                ("http://librarysimplified.org/rel/designated-agent/copyright",
                 "No valid copyright designated agent email address"),
                ("help", "No valid patron help email address")
        ):
            # Start with a valid document.
            auth_document = self._auth_document()

            # Remove the crucial link.
            auth_document['links'] = filter(
            lambda x: x['rel'] != rel or not x['href'].startswith("mailto:"),
                auth_document['links']
            )

            def _request_fails():
                self.http_client.queue_response(200, content=json.dumps(auth_document))
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
        
    def test_register_success(self):
        opds_directory = "application/opds+json;profile=https://librarysimplified.org/rel/profile/directory"

        # Pretend we are a library with a valid authentication document.
        key = RSA.generate(1024)
        auth_document = self._auth_document(key)
        self.http_client.queue_response(200, content=json.dumps(auth_document))
        self.queue_opds_success()

        auth_url = "http://circmanager.org/authentication.opds"
        opds_url = "http://circmanager.org/feed/"

        # Send a registration request to the registry.
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
            eq_(Library.REGISTERED, library.stage)

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
            eq_(6, len(library.short_name))
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

        # A human inspects the library, verifies that everything
        # works, and makes it LIVE.
        library.stage = Library.LIVE

        # Later, the library's information changes.
        auth_document = {
            "id": auth_url,
            "name": "A Library",
            "service_description": "New and improved",
            "links": [
                {"rel": "logo", "href": "/logo.png", "type": "image/png" },
                {"rel": "start", "href": "http://circmanager.org/feed/", "type": "application/atom+xml;profile=opds-catalog"},
                {"rel": "help", "href": "mailto:new-help@library.org"},
                {"rel": "http://librarysimplified.org/rel/designated-agent/copyright", "href": "mailto:new-dmca@library.org"},

            ],
            "service_area": { "US": "Connecticut" },
        }
        self.http_client.queue_response(200, content=json.dumps(auth_document))
        self.queue_opds_success()

        # We have a new logo as well.
        image_data = '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x01\x03\x00\x00\x00%\xdbV\xca\x00\x00\x00\x06PLTE\xffM\x00\x01\x01\x01\x8e\x1e\xe5\x1b\x00\x00\x00\x01tRNS\xcc\xd24V\xfd\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'
        self.http_client.queue_response(200, content=image_data, media_type="image/png")

        # So the library re-registers itself, and gets an updated
        # registry entry.
        with self.app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict([
                ("url", auth_url),
                ("contact", "mailto:me@library.org"),
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
            # The library's stage is still LIVE, it has not gone back to
            # REGISTERED.
            eq_(Library.LIVE, library.stage)

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
            eq_("mailto:new-dmca@library.org", copyright_agent_link.href)

            # The link that hasn't changed is unaffected.
            eq_(Hyperlink.INTEGRATION_CONTACT_REL, integration_contact_link.rel)
            eq_("mailto:me@library.org", integration_contact_link.href)
            
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


        # If we include the old secret in a request, the registry will
        # generate a new secret.
        with self.app.test_request_context("/", headers={"Authorization": "Bearer %s" % old_secret}, method="POST"):
            flask.request.form = ImmutableMultiDict([
                ("url", "http://circmanager.org/authentication.opds"),
                ("contact", "mailto:me@library.org"),
            ])

            key = RSA.generate(1024)
            auth_document = self._auth_document(key)
            self.http_client.queue_response(200, content=json.dumps(auth_document))
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

        # If we include an incorrect secret in the request, the secret stays the same.
        with self.app.test_request_context("/", headers={"Authorization": "Bearer notthesecret"}):
            flask.request.form = ImmutableMultiDict([
                ("url", "http://circmanager.org/authentication.opds"),
                ("contact", "mailto:me@library.org"),
            ])

            key = RSA.generate(1024)
            auth_document = self._auth_document(key)
            self.http_client.queue_response(200, content=json.dumps(auth_document))
            self.queue_opds_success()

            response = self.controller.register(do_get=self.http_client.do_get)

            eq_(200, response.status_code)
            eq_(old_secret, library.shared_secret)

    def test_opds_response_links(self):
        """Test the opds_response_links method.

        This method is used to find the link back from the OPDS document to
        the Authentication For OPDS document.

        It checks the Link header and the body of an OPDS 1 or OPDS 2
        document.

        This test also tests the related
        opds_response_links_to_auth_document, which checks whether a
        particular URL is found in the list of links.
        """
        auth_url = "http://circmanager.org/auth"
        rel = AuthenticationDocument.AUTHENTICATION_DOCUMENT_REL

        # An OPDS 1 feed that has a link.
        has_link_feed = '<feed><link rel="%s" href="%s"/></feed>' % (
            rel, auth_url
        )
        response = DummyHTTPResponse(
            200, {"Content-Type": OPDSCatalog.OPDS_1_TYPE}, has_link_feed
        )
        eq_([auth_url], LibraryRegistryController.opds_response_links(
            response, rel
        ))
        eq_(True, 
            LibraryRegistryController.opds_response_links_to_auth_document(
                response, auth_url
            )
        )
        eq_(False, 
            LibraryRegistryController.opds_response_links_to_auth_document(
                response, "Some other URL"
            )
        )

        # The same feed, but with an additional link in the
        # Link header. Both links are returned.
        response = DummyHTTPResponse(
            200, {"Content-Type": OPDSCatalog.OPDS_1_TYPE},
            has_link_feed, links={rel: {'url': "http://another-auth-document",
                                        'rel': rel}}
        )
        eq_(set([auth_url, "http://another-auth-document"]),
            set(LibraryRegistryController.opds_response_links(response, rel))
        )
        eq_(True, 
            LibraryRegistryController.opds_response_links_to_auth_document(
                response, auth_url
            )
        )

        # A similar feed, but with a relative URL, which is made absolute
        # by opds_response_links.
        relative_url_feed = '<feed><link rel="%s" href="auth-document"/></feed>' % (
            rel
        )
        response = DummyHTTPResponse(
            200, {"Content-Type": OPDSCatalog.OPDS_1_TYPE}, relative_url_feed
        )
        response.url = "http://opds-server/catalog.opds"
        eq_(["http://opds-server/auth-document"],
            LibraryRegistryController.opds_response_links(response, rel)
        )
        eq_(True, 
            LibraryRegistryController.opds_response_links_to_auth_document(
                response, "http://opds-server/auth-document"
            )
        )

        # An OPDS 1 feed that has no link.
        response = DummyHTTPResponse(
            200, {"Content-Type": OPDSCatalog.OPDS_1_TYPE}, "<feed></feed>"
        )
        eq_([], LibraryRegistryController.opds_response_links(
            response, rel
        ))
        eq_(False, 
            LibraryRegistryController.opds_response_links_to_auth_document(
                response, auth_url
            )
        )

        # An OPDS 2 feed that has a link.
        catalog = json.dumps({"links": {rel: { "href": auth_url }}})
        response = DummyHTTPResponse(
            200, {"Content-Type": OPDSCatalog.OPDS_TYPE}, catalog
        )
        eq_([auth_url], LibraryRegistryController.opds_response_links(
            response, rel
        ))
        eq_(True, 
            LibraryRegistryController.opds_response_links_to_auth_document(
                response, auth_url
            )
        )

        # An OPDS 2 feed that has no link.
        catalog = json.dumps({"links": {}})
        response = DummyHTTPResponse(
            200, {"Content-Type": OPDSCatalog.OPDS_TYPE}, catalog
        )
        eq_([], LibraryRegistryController.opds_response_links(
            response, rel
        ))
        eq_(False, 
            LibraryRegistryController.opds_response_links_to_auth_document(
                response, auth_url
            )
        )

        # A malformed feed.
        response = DummyHTTPResponse(
            200, {"Content-Type": OPDSCatalog.OPDS_TYPE}, "Not a real feed"
        )
        eq_(False, 
            LibraryRegistryController.opds_response_links_to_auth_document(
                response, auth_url
            )
        )

        # An Authentication For OPDS document.
        response = DummyHTTPResponse(
            200, {"Content-Type": AuthenticationDocument.MEDIA_TYPE}, 
            json.dumps({ "id": auth_url })
        )
        eq_([auth_url], LibraryRegistryController.opds_response_links(
            response, rel
        ))
        eq_(True, 
            LibraryRegistryController.opds_response_links_to_auth_document(
                response, auth_url
            )
        )

        # A malformed Authentication For OPDS document.
        response = DummyHTTPResponse(
            200, {"Content-Type": AuthenticationDocument.MEDIA_TYPE}, 
            json.dumps("Not a document.")
        )
        eq_([], LibraryRegistryController.opds_response_links(
            response, rel
        ))
        eq_(False, 
            LibraryRegistryController.opds_response_links_to_auth_document(
                response, auth_url
            )
        )

    def test__required_email_address(self):
        """Validate the code that makes sure an input is a mailto: URI."""
        uri = INVALID_CONTACT_URI.uri
        m = LibraryRegistryController._required_email_address

        problem = m(None, 'a title')
        eq_(uri, problem.uri)
        # The custom title is used.
        eq_("a title", problem.title)
        eq_("No email address was provided", problem.detail)

        # Changing the title doesn't affect the original ProblemDetail
        # document.
        assert "a title" != INVALID_CONTACT_URI.title

        problem = m("http://not-an-email/", "a title")
        eq_(uri, problem.uri)
        eq_("URI must start with 'mailto:' (got: http://not-an-email/)",
            problem.detail)

        mailto = "mailto:me@library.org"
        success = m(mailto, "a title")
        eq_(mailto, success)

    def test__locate_email_addresses(self):
        """Test the code that finds an email address in a list of links."""
        uri = INVALID_CONTACT_URI.uri
        m = LibraryRegistryController._locate_email_addresses

        # No links at all.
        result = m("rel0", [], "a title")
        assert isinstance(result, ProblemDetail)
        eq_(uri, result.uri)
        eq_("a title", result.title)
        eq_("No valid mailto: links found with rel=rel0", result.detail)

        # Links exist but none are valid and relevant.
        links = [dict(rel="rel1", href="http://foo/"),
                 dict(rel="rel1", href="http://bar/"),
                 dict(rel="rel2", href="mailto:me@library.org"),
                 dict(rel="rel2", href="mailto:me2@library.org"),
        ]
        result = m("rel1", links, "a title")
        assert isinstance(result, ProblemDetail)
        eq_(uri, result.uri)
        eq_("a title", result.title)
        eq_("No valid mailto: links found with rel=rel1", result.detail)

        # Multiple links that work.
        result = m("rel2", links, "a title")
        eq_(["mailto:me@library.org", "mailto:me2@library.org"], result)
