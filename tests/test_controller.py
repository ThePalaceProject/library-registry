from nose.tools import (
    eq_,
    set_trace,
)
import os
import feedparser

from controller import (
    LibraryRegistry,
    LibraryRegistryController,
)

from flask import Response

from . import DatabaseTest

from opds import OPDSFeed

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

            # We found both libraries within a 150-kilometer radius of the
            # starting point.
            nypl, ct = feed['entries']
            eq_("NYPL", nypl['title'])
            eq_("0 km.", nypl['schema_distance'])
            eq_("Connecticut State Library", ct['title'])
            eq_("35 km.", ct['schema_distance'])

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
