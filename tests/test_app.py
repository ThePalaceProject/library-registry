from io import BytesIO
import contextlib
import flask
import gzip
from app_helpers import (
    compressible,
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

    def test_compressible(self):
        # Prepare a value and a gzipped version of the value.
        value = "Compress me! (Or not.)"

        buffer = BytesIO()
        gzipped = gzip.GzipFile(mode='wb', fileobj=buffer)
        gzipped.write(value)
        gzipped.close()
        compressed = buffer.getvalue()

        # Spot-check the compressed value
        assert '-(J-.V' in compressed

        # This compressible controller function always returns the
        # same value.
        @compressible
        def function():
            return value

        def ask_for_compression(compression, header='Accept-Encoding'):
            """This context manager simulates the entire Flask
            request-response cycle, including a call to
            process_response(), which triggers the @after_this_request
            hooks.

            :return: The Response object.
            """
            headers = {}
            if compression:
                headers[header] = compression
            with self.app.test_request_context(headers=headers):
                response = flask.Response(function())
                self.app.process_response(response)
                return response

        # If the client asks for gzip through Accept-Encoding, the
        # representation is compressed.
        response = ask_for_compression("gzip")
        eq_(compressed, response.data)
        eq_("gzip", response.headers['Content-Encoding'])

        # If the client doesn't ask for compression, the value is
        # passed through unchanged.
        response = ask_for_compression(None)
        eq_(value, response.data)
        assert 'Content-Encoding' not in response.headers

        # Similarly if the client asks for an unsupported compression
        # mechanism.
        response = ask_for_compression('compress')
        eq_(value, response.data)
        assert 'Content-Encoding' not in response.headers

        # Or if the client asks for a compression mechanism through
        # Accept-Transfer-Encoding, which is currently unsupported.
        response = ask_for_compression("gzip", "Accept-Transfer-Encoding")
        eq_(value, response.data)
        assert 'Content-Encoding' not in response.headers

