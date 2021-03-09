from io import BytesIO
import gzip

import flask
import pytest

from library_registry.app_helpers import (
    compressible,
    has_library_factory,
    uses_location_factory,
)
from library_registry.problem_details import LIBRARY_NOT_FOUND
from .test_controller import ControllerTest


class TestAppHelpers(ControllerTest):
    def test_has_library(self):
        has_library = has_library_factory(self.app)

        @has_library
        def route_function():
            return "Called with library %s" % flask.request.library.name

        def assert_not_found(uuid):
            response = route_function(uuid)
            assert response == LIBRARY_NOT_FOUND.response

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
                assert response == "Called with library NYPL"

    def test_uses_location(self):
        uses_location = uses_location_factory(self.app)

        @uses_location
        def route_function(_location):
            return "Called with location %s" % _location

        with self.app.test_request_context():
            assert route_function() == "Called with location None"

        with self.app.test_request_context("/?_location=-10,10"):
            assert route_function() == "Called with location SRID=4326;POINT (10.0 -10.0)"

    def test_compressible(self):
        # Prepare a value and a gzipped version of the value.
        value = b"Compress me! (Or not.)"

        buffer = BytesIO()
        gzipped = gzip.GzipFile(mode='wb', fileobj=buffer)
        gzipped.write(value)
        gzipped.close()
        compressed = buffer.getvalue()

        # Spot-check the compressed value
        assert b'-(J-.V' in compressed

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
        assert response.data == compressed
        assert response.headers['Content-Encoding'] == "gzip"

        # If the client doesn't ask for compression, the value is
        # passed through unchanged.
        response = ask_for_compression(None)
        assert response.data == value
        assert 'Content-Encoding' not in response.headers

        # Similarly if the client asks for an unsupported compression
        # mechanism.
        response = ask_for_compression('compress')
        assert response.data == value
        assert 'Content-Encoding' not in response.headers

        # Or if the client asks for a compression mechanism through
        # Accept-Transfer-Encoding, which is currently unsupported.
        response = ask_for_compression("gzip", "Accept-Transfer-Encoding")
        assert response.data == value
        assert 'Content-Encoding' not in response.headers
