import flask
from app_helpers import (
    has_library_factory,
    uses_location_factory,
)
from nose.tools import (
    eq_,
    set_trace,
)
from problem_details import (
    LIBRARY_NOT_FOUND
)
from test_controller import ControllerTest
from testing import DatabaseTest

class TestAppHelpers(ControllerTest):

    def test_has_library(self):
        has_library = has_library_factory(self.app)

        @has_library
        def route_function():
            return "Called with library %s" % flask.request.library.name

        def assert_not_found(uuid):
            response = route_function(uuid)
            eq_(LIBRARY_NOT_FOUND.response, response)

        assert_not_found(uuid=None)
        assert_not_found(uuid="no such library")
        library = self.nypl

        urns = [
            library.internal_urn,
            library.internal_urn[len("urn:uuid:"):]
        ]
        for urn in urns:
            with self.app.test_request_context():
                response = route_function(uuid=urn)
                eq_("Called with library NYPL", response)

    def test_uses_location(self):
        uses_location = uses_location_factory(self.app)

        @uses_location
        def route_function(_location):
            return "Called with location %s" % _location

        with self.app.test_request_context():
            eq_("Called with location None", route_function())

        with self.app.test_request_context("/?_location=-10,10"):
            eq_("Called with location SRID=4326;POINT (10.0 -10.0)",
                route_function())
