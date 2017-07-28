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

from opds import OPDSCatalog
from model import (
  get_one,
  get_one_or_create,
  ExternalIntegration,
  Library,
)
from problem_details import *
from config import Configuration


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
            
    def test_nearby_no_ip_address(self):
        with self.app.test_request_context("/"):
            response = self.controller.nearby(None)
            assert isinstance(response, Response)
            eq_("200 OK", response.status)
            eq_(OPDSCatalog.OPDS_TYPE, response.headers['Content-Type'])
            catalog = json.loads(response.data)

            # We found no nearby libraries, because we had no IP address to
            # start with.
            eq_([], catalog['catalogs'])

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

    def test_register_success(self):
        http_client = DummyHTTPClient()
        opds1_type = "application/atom+xml;profile=opds-catalog;kind=acquisition"
        opds2_type = "application/opds+json"

        # Register a new library.
        with self.app.test_request_context("/"):
            flask.request.form = ImmutableMultiDict([
                ("url", "http://circmanager.org"),
            ])

            key = RSA.generate(1024)
            auth_document = {
                "name": "A Library",
                "service_description": "Description",
                "links": {
                    "alternate": { "href": "http://alibrary.org", "type": "text/html" },
                    "logo": { "href": "data:image/png;imagedata" },
                },
                "service_area": { "US": "Kansas" },
                "public_key": {
                    "type": "RSA",
                    "value": key.publickey().exportKey(),
                 }
            }
            http_client.queue_response(401, content=json.dumps(auth_document))

            response = self.controller.register(do_get=http_client.do_get)

            eq_(201, response.status_code)
            eq_("application/opds+json;profile=https://librarysimplified.org/rel/profile/directory",
                response.headers.get("Content-Type"))

            library = get_one(self._db, Library, opds_url="http://circmanager.org")
            assert library != None
            eq_("A Library", library.name)
            eq_("Description", library.description)
            eq_("http://alibrary.org", library.web_url)
            eq_("data:image/png;imagedata", library.logo)
            [service_area] = library.service_areas
            eq_(self.kansas_state.id, service_area.place_id)

            eq_(["http://circmanager.org"], http_client.requests)

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

        # Register changes to the same library, and test all the places
        # the auth document could be.
        with self.app.test_request_context("/"):
            flask.request.form = ImmutableMultiDict([
                ("url", "http://circmanager.org"),
            ])

            image_data = '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x01\x03\x00\x00\x00%\xdbV\xca\x00\x00\x00\x06PLTE\xffM\x00\x01\x01\x01\x8e\x1e\xe5\x1b\x00\x00\x00\x01tRNS\xcc\xd24V\xfd\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'
            http_client.queue_response(200, content=image_data, media_type="image/png")
            auth_document = {
                "name": "A Library",
                "service_description": "My feed requires authentication",
                "links": {
                    "logo": { "href": "http://example.com/logo.png", "type": "image/png" },
                },
                "service_area": { "US": "Connecticut" },
            }
            http_client.queue_response(401, content=json.dumps(auth_document))

            response = self.controller.register(do_get=http_client.do_get)
            eq_(200, response.status_code)
            eq_("application/opds+json;profile=https://librarysimplified.org/rel/profile/directory",
                response.headers.get("Content-Type"))

            library = get_one(self._db, Library, opds_url="http://circmanager.org")
            assert library != None
            eq_("A Library", library.name)
            eq_("My feed requires authentication", library.description)
            eq_(None, library.web_url)
            eq_("data:image/png;base64,%s" % base64.b64encode(image_data), library.logo)
            # Commit to update library.service_areas.
            self._db.commit()
            [service_area] = library.service_areas
            eq_(self.connecticut_state.id, service_area.place_id)
            eq_(["http://circmanager.org", "http://example.com/logo.png"], http_client.requests[1:])

            catalog = json.loads(response.data)
            eq_("A Library", catalog['metadata']['title'])
            eq_('My feed requires authentication', catalog['metadata']['description'])

            auth_document = {
                "name": "A Library",
                "service_description": "My feed links to the auth document",
            }
            http_client.queue_response(200, content=json.dumps(auth_document))
            feed = '<feed><link rel="http://opds-spec.org/auth/document" href="http://circmanager.org/auth"/></feed>'
            http_client.queue_response(200, content=feed, media_type=opds1_type)

            response = self.controller.register(do_get=http_client.do_get)
            eq_(200, response.status_code)
            eq_("My feed links to the auth document", library.description)
            eq_(["http://circmanager.org", "http://circmanager.org/auth"], http_client.requests[3:])

            auth_document = {
                "name": "A Library",
                "service_description": "My catalog links to the auth document",
            }
            http_client.queue_response(200, content=json.dumps(auth_document))
            catalog = json.dumps({
                "links": {
                    "http://opds-spec.org/auth/document": {
                        "href": "http://circmanager.org/auth"
                    }
                }
            })
            http_client.queue_response(200, content=catalog, media_type=opds2_type)

            response = self.controller.register(do_get=http_client.do_get)
            eq_(200, response.status_code)
            eq_("My catalog links to the auth document", library.description)
            eq_(["http://circmanager.org", "http://circmanager.org/auth"], http_client.requests[5:])

            auth_document = {
                "name": "A Library",
                "service_description": "My feed links to the shelf, which requires auth",
            }
            http_client.queue_response(401, content=json.dumps(auth_document))
            feed = '<feed><link rel="http://opds-spec.org/shelf" href="http://circmanager.org/shelf"/></feed>'
            http_client.queue_response(200, content=feed, media_type=opds1_type)

            response = self.controller.register(do_get=http_client.do_get)
            eq_(200, response.status_code)
            eq_("My feed links to the shelf, which requires auth", library.description)
            eq_(["http://circmanager.org", "http://circmanager.org/shelf"], http_client.requests[7:])
            
            auth_document = {
                "name": "A Library",
                "service_description": "My feed links to a shelf which links to the auth document",
            }
            http_client.queue_response(200, content=json.dumps(auth_document))
            shelf_feed = '<feed><link rel="http://opds-spec.org/auth/document" href="http://circmanager.org/auth"/></feed>'
            http_client.queue_response(200, content=shelf_feed, media_type=opds1_type)
            feed = '<feed><link rel="http://opds-spec.org/shelf" href="http://circmanager.org/shelf"/></feed>'
            http_client.queue_response(200, content=feed, media_type=opds1_type)

            response = self.controller.register(do_get=http_client.do_get)
            eq_(200, response.status_code)
            eq_("My feed links to a shelf which links to the auth document", library.description)
            eq_(["http://circmanager.org", "http://circmanager.org/shelf", "http://circmanager.org/auth"], http_client.requests[9:])

            # The request did not include the old secret, so the secret wasn't changed.
            eq_(old_secret, library.shared_secret)

        # If we include the old secret in a request, the registry will generate a new secret.
        with self.app.test_request_context("/"):
            flask.request.form = ImmutableMultiDict([
                ("url", "http://circmanager.org"),
                ("shared_secret", old_secret),
            ])

            key = RSA.generate(1024)
            auth_document = {
                "name": "A Library",
                "service_description": "Description",
                "links": {
                    "alternate": { "href": "http://alibrary.org", "type": "text/html" },
                    "logo": { "href": "data:image/png;imagedata" },
                },
                "public_key": {
                    "type": "RSA",
                    "value": key.publickey().exportKey(),
                 }
            }
            http_client.queue_response(401, content=json.dumps(auth_document))

            response = self.controller.register(do_get=http_client.do_get)

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
        with self.app.test_request_context("/"):
            flask.request.form = ImmutableMultiDict([
                ("url", "http://circmanager.org"),
                ("shared_secret", "not the secret"),
            ])

            key = RSA.generate(1024)
            auth_document = {
                "name": "A Library",
                "service_description": "Description",
                "links": {
                    "alternate": { "href": "http://alibrary.org", "type": "text/html" },
                    "logo": { "href": "data:image/png;imagedata" },
                },
                "public_key": {
                    "type": "RSA",
                    "value": key.publickey().exportKey(),
                 }
            }
            http_client.queue_response(401, content=json.dumps(auth_document))

            response = self.controller.register(do_get=http_client.do_get)

            eq_(200, response.status_code)
            eq_(old_secret, library.shared_secret)

    def test_register_errors(self):
        http_client = DummyHTTPClient()
        opds1_type = "application/atom+xml;profile=opds-catalog;kind=acquisition"
        opds2_type = "application/opds+json"

        with self.app.test_request_context("/"):
            response = self.controller.register(do_get=http_client.do_get)

            eq_(NO_OPDS_URL, response)

        with self.app.test_request_context("/"):
            flask.request.form = ImmutableMultiDict([
                ("url", "http://circmanager.org"),
            ])

            # This feed doesn't work.
            http_client.queue_response(500)
            response = self.controller.register(do_get=http_client.do_get)
            eq_(INVALID_OPDS_FEED, response)

            # This feed claims to be OPDS 2 but isn't valid JSON.
            http_client.queue_response(200, content="not json", media_type=opds2_type)
            response = self.controller.register(do_get=http_client.do_get)
            eq_(INVALID_OPDS_FEED, response)

            # This feed doesn't link to the auth document or the shelf.
            opds_feed = '<feed></feed>'
            http_client.queue_response(200, content=opds_feed, media_type=opds1_type)
            response = self.controller.register(do_get=http_client.do_get)
            eq_(AUTH_DOCUMENT_NOT_FOUND, response)

            # This feed links to the auth document, but that link is broken.
            opds_feed = '<feed><link rel="http://opds-spec.org/auth/document" href="broken"/></feed>'
            http_client.queue_response(404)
            http_client.queue_response(200, content=opds_feed, media_type=opds1_type)
            response = self.controller.register(do_get=http_client.do_get)
            eq_(AUTH_DOCUMENT_NOT_FOUND, response)

            # This feed links to the shelf, but it doesn't require auth and it
            # doesn't link to the auth document.
            shelf_feed = '<feed></feed>'
            opds_feed = '<feed><link rel="http://opds-spec.org/shelf" href="http://circmanager.org/shelf"/></feed>'
            http_client.queue_response(200, content=shelf_feed, media_type=opds1_type)
            http_client.queue_response(200, content=opds_feed, media_type=opds1_type)
            response = self.controller.register(do_get=http_client.do_get)
            eq_(AUTH_DOCUMENT_NOT_FOUND, response)

            # This feed has an auth document that's not valid.
            http_client.queue_response(401, content="not json")
            opds_feed = '<feed><link href="http://circmanager.org/shelf" rel="http://opds-spec.org/shelf"/></feed>'
            http_client.queue_response(200, content=opds_feed, media_type=opds1_type)
            response = self.controller.register(do_get=http_client.do_get)
            eq_(INVALID_AUTH_DOCUMENT, response)

            # This feed has an unknown service area.
            auth_document = {
                "name": "A Library",
                "service_area": {"US": ["Somewhere"]},
            }
            http_client.queue_response(401, content=json.dumps(auth_document))
            response = self.controller.register(do_get=http_client.do_get)
            eq_(INVALID_AUTH_DOCUMENT.uri, response.uri)
            eq_("The following service area was unknown: {\"US\": [\"Somewhere\"]}.", response.detail)

            # This feed has an ambiguous service area.
            auth_document = {
                "name": "A Library",
                "service_area": {"US": ["Manhattan"]},
            }
            http_client.queue_response(401, content=json.dumps(auth_document))
            response = self.controller.register(do_get=http_client.do_get)
            eq_(INVALID_AUTH_DOCUMENT.uri, response.uri)
            eq_("The following service area was ambiguous: {\"US\": [\"Manhattan\"]}.", response.detail)

            # This feed links to a broken logo image.
            http_client.queue_response(500)
            auth_document = {
                "name": "A Library",
                "links": {
                    "logo": { "href": "http://example.com/logo.png", "type": "image/png" },
                },
            }
            http_client.queue_response(401, content=json.dumps(auth_document))
            response = self.controller.register(do_get=http_client.do_get)
            eq_(INVALID_AUTH_DOCUMENT.uri, response.uri)



