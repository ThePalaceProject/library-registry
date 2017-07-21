from nose.tools import (
    eq_,
    set_trace,
)
import os
import feedparser
import json

from controller import (
    LibraryRegistry,
    LibraryRegistryController,
)

import flask
from flask import Response
from werkzeug import ImmutableMultiDict

from . import DatabaseTest
from testing import DummyHTTPClient

from opds import OPDSFeed
from model import (
  get_one,
  Library,
)
from problem_details import *


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
        
        self.library_registry = TestLibraryRegistry(self._db, testing=True)
        self.app.library_registry = self.library_registry
        self.controller = LibraryRegistryController(self.library_registry)


class TestLibraryRegistryController(ControllerTest):

    def test_nearby(self):
        with self.app.test_request_context("/"):
            response = self.controller.nearby("65.88.88.124")
            assert isinstance(response, Response)
            eq_("200 OK", response.status)
            eq_(OPDSFeed.NAVIGATION_FEED_TYPE, response.headers['Content-Type'])
            feed = feedparser.parse(response.data)

            # The feed can be cached for a while, since the list of libraries
            # doesn't change very quickly.
            eq_("public, no-transform, max-age: 43200, s-maxage: 21600",
                response.headers['Cache-Control'])

            # We found both libraries within a 150-kilometer radius of the
            # starting point.
            nypl, ct = feed['entries']
            eq_("NYPL", nypl['title'])
            eq_("0 km.", nypl['schema_distance'])
            eq_("Connecticut State Library", ct['title'])
            eq_("35 km.", ct['schema_distance'])

            # If that's not good enough, there's a link to the search
            # controller, so you can do a search.
            [register_link, search_link, self_link] = sorted(
                feed['feed']['links'], key=lambda x: x['rel']
            )
            url_for = self.app.library_registry.url_for

            eq_(url_for("nearby"), self_link['href'])
            eq_("self", self_link['rel'])
            eq_(OPDSFeed.NAVIGATION_FEED_TYPE, self_link['type'])

            eq_(url_for("search"), search_link['href'])
            eq_("search", search_link['rel'])
            eq_("application/opensearchdescription+xml", search_link['type'])

            eq_(url_for("register"), register_link["href"])
            eq_("register", register_link["rel"])
            
    def test_nearby_no_ip_address(self):
        with self.app.test_request_context("/"):
            response = self.controller.nearby(None)
            assert isinstance(response, Response)
            eq_("200 OK", response.status)
            eq_(OPDSFeed.NAVIGATION_FEED_TYPE, response.headers['Content-Type'])
            feed = feedparser.parse(response.data)

            # We found no nearby libraries, because we had no IP address to
            # start with.
            eq_([], feed['entries'])

    def test_nearby_no_libraries(self):
        with self.app.test_request_context("/"):
            response = self.controller.nearby("8.8.8.8") # California
            assert isinstance(response, Response)
            eq_("200 OK", response.status)
            eq_(OPDSFeed.NAVIGATION_FEED_TYPE, response.headers['Content-Type'])
            feed = feedparser.parse(response.data)

            # We found no nearby libraries, because we were too far away
            # from them.
            eq_([], feed['entries'])
            
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
            eq_(OPDSFeed.NAVIGATION_FEED_TYPE, response.headers['Content-Type'])
            feed = feedparser.parse(response.data)

            # We found the two matching results.
            [nypl, ks] = feed['entries']
            eq_("NYPL", nypl['title'])
            eq_("0 km.", nypl['schema_distance'])

            eq_("Kansas State Library", ks['title'])
            eq_("1922 km.", ks['schema_distance'])

            [register_link, search_link, self_link] = sorted(
                feed['feed']['links'], key=lambda x: x['rel']
            )
            url_for = self.app.library_registry.url_for

            # The search results have a self link and a link back to
            # the search form.
            eq_(url_for("search", q="manhattan"), self_link['href'])
            eq_("self", self_link['rel'])
            eq_(OPDSFeed.NAVIGATION_FEED_TYPE, self_link['type'])

            eq_(url_for("search"), search_link['href'])
            eq_("search", search_link['rel'])
            eq_("application/opensearchdescription+xml", search_link['type'])

            eq_(url_for("register"), register_link["href"])
            eq_("register", register_link["rel"])

    def test_register_success(self):
        http_client = DummyHTTPClient()

        # Register a new library.
        with self.app.test_request_context("/"):
            flask.request.form = ImmutableMultiDict([
                ("url", "http://circmanager.org"),
            ])

            auth_document = {
                "name": "A Library",
                "service_description": "Description",
                "links": {
                    "alternate": { "href": "http://alibrary.org" },
                    "logo": { "href": "image data" },
                }
            }
            http_client.queue_response(401, content=json.dumps(auth_document))

            response = self.controller.register(do_get=http_client.do_get)

            eq_(201, response.status_code)

            library = get_one(self._db, Library, opds_url="http://circmanager.org")
            assert library != None
            eq_("A Library", library.name)
            eq_("Description", library.description)
            eq_("http://alibrary.org", library.web_url)
            eq_("image data", library.logo)

            eq_(["http://circmanager.org"], http_client.requests)


        # Register changes to the same library, and test all the places
        # the auth document could be.
        with self.app.test_request_context("/"):
            flask.request.form = ImmutableMultiDict([
                ("url", "http://circmanager.org"),
            ])

            auth_document = {
                "name": "A Library",
                "service_description": "My feed requires authentication",
                "links": {
                    "logo": { "href": "new image data" },
                }
            }
            http_client.queue_response(401, content=json.dumps(auth_document))

            response = self.controller.register(do_get=http_client.do_get)
            eq_(200, response.status_code)

            library = get_one(self._db, Library, opds_url="http://circmanager.org")
            assert library != None
            eq_("A Library", library.name)
            eq_("My feed requires authentication", library.description)
            eq_(None, library.web_url)
            eq_("new image data", library.logo)
            eq_(["http://circmanager.org"], http_client.requests[1:])

            auth_document = {
                "name": "A Library",
                "service_description": "My feed links to the auth document",
            }
            http_client.queue_response(200, content=json.dumps(auth_document))
            feed = '<feed><link rel="http://opds-spec.org/auth/document" href="http://circmanager.org/auth"/></feed>'
            http_client.queue_response(200, content=feed)

            response = self.controller.register(do_get=http_client.do_get)
            eq_(200, response.status_code)
            eq_("My feed links to the auth document", library.description)
            eq_(["http://circmanager.org", "http://circmanager.org/auth"], http_client.requests[2:])

            auth_document = {
                "name": "A Library",
                "service_description": "My feed links to the shelf, which requires auth",
            }
            http_client.queue_response(401, content=json.dumps(auth_document))
            feed = '<feed><link rel="http://opds-spec.org/shelf" href="http://circmanager.org/shelf"/></feed>'
            http_client.queue_response(200, content=feed)

            response = self.controller.register(do_get=http_client.do_get)
            eq_(200, response.status_code)
            eq_("My feed links to the shelf, which requires auth", library.description)
            eq_(["http://circmanager.org", "http://circmanager.org/shelf"], http_client.requests[4:])
            
            auth_document = {
                "name": "A Library",
                "service_description": "My feed links to a shelf which links to the auth document",
            }
            http_client.queue_response(200, content=json.dumps(auth_document))
            shelf_feed = '<feed><link rel="http://opds-spec.org/auth/document" href="http://circmanager.org/auth"/></feed>'
            http_client.queue_response(200, content=shelf_feed)
            feed = '<feed><link rel="http://opds-spec.org/shelf" href="http://circmanager.org/shelf"/></feed>'
            http_client.queue_response(200, content=feed)

            response = self.controller.register(do_get=http_client.do_get)
            eq_(200, response.status_code)
            eq_("My feed links to a shelf which links to the auth document", library.description)
            eq_(["http://circmanager.org", "http://circmanager.org/shelf", "http://circmanager.org/auth"], http_client.requests[6:])


    def test_register_errors(self):
        http_client = DummyHTTPClient()

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

            # This feed doesn't link to the auth document or the shelf.
            opds_feed = '<feed></feed>'
            http_client.queue_response(200, content=opds_feed)
            response = self.controller.register(do_get=http_client.do_get)
            eq_(AUTH_DOCUMENT_NOT_FOUND, response)

            # This feed links to the auth document, but that link is broken.
            opds_feed = '<feed><link rel="http://opds-spec.org/auth/document" href="broken"/></feed>'
            http_client.queue_response(404)
            http_client.queue_response(200, content=opds_feed)
            response = self.controller.register(do_get=http_client.do_get)
            eq_(AUTH_DOCUMENT_NOT_FOUND, response)

            # This feed links to the shelf, but it doesn't require auth and it
            # doesn't link to the auth document.
            shelf_feed = '<feed></feed>'
            opds_feed = '<feed><link rel="http://opds-spec.org/shelf" href="http://circmanager.org/shelf"/></feed>'
            http_client.queue_response(200, content=shelf_feed)
            http_client.queue_response(200, content=opds_feed)
            response = self.controller.register(do_get=http_client.do_get)
            eq_(AUTH_DOCUMENT_NOT_FOUND, response)

            # This feed has an auth document that's not valid.
            http_client.queue_response(401, content="not json")
            opds_feed = '<feed><link href="http://circmanager.org/shelf" rel="http://opds-spec.org/shelf"/></feed>'
            http_client.queue_response(200, content=opds_feed)
            response = self.controller.register(do_get=http_client.do_get)
            eq_(INVALID_AUTH_DOCUMENT, response)



