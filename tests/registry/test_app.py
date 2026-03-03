import gzip
from io import BytesIO

import flask

from palace.registry.app_helpers import (
    compressible,
    has_library_factory,
    require_admin_authentication,
    uses_location_factory,
)
from palace.registry.problem_details import LIBRARY_NOT_FOUND
from palace.registry.sqlalchemy.model.admin import Admin
from tests.fixtures.controller import ControllerSetupFixture


class TestAppHelpers:
    def test_has_library(self, controller_setup_fixture: ControllerSetupFixture):
        with controller_setup_fixture.setup() as fixture:
            has_library = has_library_factory(fixture.app)

            @has_library
            def route_function():
                return "Called with library %s" % flask.request.library.name

            def assert_not_found(uuid):
                response = route_function(uuid)
                assert response == LIBRARY_NOT_FOUND.response

            assert_not_found(uuid=None)
            assert_not_found(uuid="no such library")
            library = fixture.db.nypl

            urns = [library.internal_urn, library.internal_urn[len("urn:uuid:") :]]
            for urn in urns:
                with fixture.app.test_request_context():
                    response = route_function(uuid=urn)
                    assert response == "Called with library NYPL"

    def test_uses_location(self, controller_setup_fixture: ControllerSetupFixture):
        with controller_setup_fixture.setup() as fixture:
            uses_location = uses_location_factory(fixture.app)

            @uses_location
            def route_function(_location):
                return "Called with location %s" % _location

            with fixture.app.test_request_context():
                assert route_function() == "Called with location None"

            with fixture.app.test_request_context("/?_location=-10,10"):
                assert (
                    route_function()
                    == "Called with location SRID=4326;POINT(10.0 -10.0)"
                )

    def test_compressible(self, controller_setup_fixture: ControllerSetupFixture):
        with controller_setup_fixture.setup() as fixture:
            # Prepare a value and a gzipped version of the value.
            value = b"Compress me! (Or not.)"

            buffer = BytesIO()
            gzipped = gzip.GzipFile(mode="wb", fileobj=buffer)
            gzipped.write(value)
            gzipped.close()
            compressed = buffer.getvalue()

            # Spot-check the compressed value
            assert b"-(J-.V" in compressed

            # This compressible controller function always returns the
            # same value.
            @compressible
            def function():
                return value

            def ask_for_compression(compression, header="Accept-Encoding"):
                """This context manager simulates the entire Flask
                request-response cycle, including a call to
                process_response(), which triggers the @after_this_request
                hooks.

                :return: The Response object.
                """
                headers = {}
                if compression:
                    headers[header] = compression
                with fixture.app.test_request_context(headers=headers):
                    response = flask.Response(function())
                    fixture.app.process_response(response)
                    return response

            # If the client asks for gzip through Accept-Encoding, the
            # representation is compressed.
            response = ask_for_compression("gzip")
            assert response.data == compressed
            assert response.headers["Content-Encoding"] == "gzip"

            # If the client doesn't ask for compression, the value is
            # passed through unchanged.
            response = ask_for_compression(None)
            assert response.data == value
            assert "Content-Encoding" not in response.headers

            # Similarly if the client asks for an unsupported compression
            # mechanism.
            response = ask_for_compression("compress")
            assert response.data == value
            assert "Content-Encoding" not in response.headers

            # Or if the client asks for a compression mechanism through
            # Accept-Transfer-Encoding, which is currently unsupported.
            response = ask_for_compression("gzip", "Accept-Transfer-Encoding")
            assert response.data == value
            assert "Content-Encoding" not in response.headers

    def test_auth_admin_only(self, controller_setup_fixture: ControllerSetupFixture):
        with controller_setup_fixture.setup() as fixture:

            @require_admin_authentication
            def test_fn():
                return True

            # This will setup the new admin, must use self._db since the test rollback occurs on it
            Admin.authenticate(fixture.db.session, "admin", "admin")
            # The app._db must be the same session as the above authenticate session so it can share the state
            fixture.app._db = fixture.db.session

            with fixture.app.test_request_context(
                "/", data=dict(username="admin", password="admin")
            ) as ctx:
                # Log in not yet done
                assert test_fn().status_code == 401

                # Log in is done, authenticated function should run
                fixture.app.library_registry.registry_controller.log_in()
                assert ctx.session["username"] == "admin"
                assert test_fn() == True

                # log out, unauthorized again
                fixture.app.library_registry.registry_controller.log_out()
                assert test_fn().status_code == 401
