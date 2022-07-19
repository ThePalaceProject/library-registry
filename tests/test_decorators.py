import gzip
import uuid
from io import BytesIO
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask, Blueprint, g, jsonify, make_response
from flask_sqlalchemy_session import current_session
from flask_jwt_extended import (
    create_access_token, JWTManager, get_jwt, set_access_cookies, get_jwt_identity)
from flask_babel import Babel


from library_registry.decorators import (
    compressible,
    has_library,
    returns_json_or_response_or_problem_detail,
    returns_problem_detail,
    uses_location
)

from library_registry.admin.decorators import check_logged_in
from library_registry.problem_details import LIBRARY_NOT_FOUND
from library_registry.util.problem_detail import ProblemDetail
from library_registry.util.geo import Location

PROBLEM_DETAIL_FOR_TEST = ProblemDetail(
    "http://localhost/", 400, "A problem happened.")
RESPONSE_JSON = {"alpha": "apple", "bravo": "banana"}
RESPONSE_OBJ_VAL = "This is a Response object."


@pytest.fixture
def app():
    app = Flask(__name__)
    babel = Babel()
    babel.init_app(app)
    app.config['JWT_SECRET_KEY'] = 'testing'
    jwt = JWTManager(app)
    app.config['JWT_TOKEN_LOCATION'] = ['cookies', 'headers']
    return(app)


@pytest.fixture
def app_with_decorated_routes(app):
    """Return an app instance augmented with routes that exercise the decorators"""
    test_blueprint = Blueprint("test_blueprint", __name__, url_prefix="/test")

    @test_blueprint.route("/uses_location")
    @uses_location
    def uses_location_testview():
        location_obj = g.get('location', None)
        return jsonify({"g.location": str(location_obj)})

    @test_blueprint.route("/has_library/<uuid>")
    @has_library
    def has_library_testview():
        return jsonify({"g.library": str(g.library)})

    @test_blueprint.route("/returns_problem_detail")
    @returns_problem_detail
    def returns_problem_detail_testview():
        return PROBLEM_DETAIL_FOR_TEST

    @test_blueprint.route("/rjoropd/json")
    @returns_json_or_response_or_problem_detail
    def returns_json_testview():
        return RESPONSE_JSON

    @test_blueprint.route("/rjoropd/response")
    @returns_json_or_response_or_problem_detail
    def returns_response_testview():
        return make_response(RESPONSE_OBJ_VAL)

    @test_blueprint.route("/rjoropd/problem_detail")
    @returns_json_or_response_or_problem_detail
    def returns_problemdetail_testview():
        return PROBLEM_DETAIL_FOR_TEST

    @test_blueprint.route("/compressible")
    @compressible
    def returns_compressed_testview():
        response = make_response(RESPONSE_OBJ_VAL)
        return response

    @test_blueprint.route("/compressible_4xx")
    @returns_problem_detail
    @compressible
    def returns_uncompressed_4xx_testview():
        return PROBLEM_DETAIL_FOR_TEST

    @test_blueprint.route("/compressible_already_encoded")
    @compressible
    def returns_already_encoded_testview():
        response = make_response(RESPONSE_OBJ_VAL)
        response.headers["Content-Encoding"] = "some_encoding"
        return response

    @test_blueprint.route('/check_logged_in')
    @check_logged_in
    def returns_logged_in_response():
        response = make_response(RESPONSE_OBJ_VAL)
        return response

    @test_blueprint.after_request
    def refresh_expiring_jwts(response):
        try:
            exp_timestamp = get_jwt()["exp"]
            now = datetime.now(timezone.utc)
            target_timestamp = datetime.timestamp(now + timedelta(minutes=30))
            if target_timestamp > exp_timestamp:
                access_token = create_access_token(identity=get_jwt_identity())
                response = make_response(response, 201)
                set_access_cookies(response, access_token)
            return response
        except (RuntimeError, KeyError):
            # Case where there is not a valid JWT. Just return the original response
            return response

    app.register_blueprint(test_blueprint)
    yield app
    del app


class TestDecorators:
    def test_uses_location_from_args(self, app_with_decorated_routes):
        """
        GIVEN: A request with a valid _location string in the args
        WHEN:  The uses_location decorator intercepts that request
        THEN:  A corresponding geometry string should be placed in g.location
        """
        with app_with_decorated_routes.test_client() as client:
            client.get("/test/uses_location?_location=40.7128,74.0060")
            assert isinstance(g.location, Location)
            assert str(g.location) == 'SRID=4326;POINT(74.006 40.7128)'

    def test_uses_location_from_ip(self, app_with_decorated_routes):
        """
        GIVEN: A request with no _location string in the args
        WHEN:  The uses_location decorator intercepts that request
        THEN:  A geometry string derived from the requesting IP should be placed in g.location
        """
        with app_with_decorated_routes.test_client() as client:
            client.get("/test/uses_location",
                       headers={"X-Forwarded-For": "1.1.1.1"})
            assert isinstance(g.location, Location)
            assert g.location.ewkt == 'SRID=4326;POINT(145.1833 -37.7)'

    def test_uses_location_bad_input(self, app_with_decorated_routes):
        """
        GIVEN: A request with an invalid _location string in the args
        WHEN:  The uses_location decorator intercepts that request
        THEN:  None should be placed in g.location
        """
        with app_with_decorated_routes.test_client() as client:
            client.get("/test/uses_location?_location=BADINPUT")
            assert g.get('location', None) is None

    @pytest.mark.skip(reason="NEEDS_FIX")
    def test_has_library_full_urn(self, app_with_decorated_routes, create_test_library):
        """
        GIVEN: A request to a route whose URL pattern includes a <uuid> parameter
               which identifies a library that exists in the database, where the
               value is formatted as 'urn:uuid:' + str(uuid.uuid4()).
        WHEN:  The has_library decorator intercepts that request
        THEN:  A corresponding Library object should be placed in g.library
        """
        with app_with_decorated_routes.app_context():
            test_lib = create_test_library(current_session)
            with app_with_decorated_routes.test_client() as client:
                bare_uuid = str(test_lib.internal_urn)[9:]
                client.get(f"/test/has_library/urn:uuid:{bare_uuid}")
                assert g.library.internal_urn == test_lib.internal_urn
            current_session.delete(test_lib)
            current_session.commit()

    @pytest.mark.skip(reason="NEEDS_FIX")
    def test_has_library_bare_uuid(self, app_with_decorated_routes, create_test_library):
        """
        GIVEN: A request to a route whose URL pattern includes a <uuid> parameter
               which identifies a library that exists in the database, where the
               value is formatted as just a str(uuid.uuid4()) without leading 'urn:uuid:'.
        WHEN:  The has_library decorator intercepts that request
        THEN:  A corresponding Library object should be placed in g.library
        """
        with app_with_decorated_routes.app_context():
            test_lib = create_test_library(current_session)
            with app_with_decorated_routes.test_client() as client:
                client.get(f"/test/has_library/{test_lib.internal_urn}")
                assert g.library.internal_urn == test_lib.internal_urn
            current_session.delete(test_lib)
            current_session.commit()

    @pytest.mark.skip(reason="NEEDS_FIX")
    def test_has_library_bad_uuid(self, app_with_decorated_routes):
        """
        GIVEN: A request to a route whose URL pattern includes a <uuid> parameter
               which is not the internal_urn value of any known Library.
        WHEN:  The has_library decorator intercepts that request
        THEN:  A LIBRARY_NOT_FOUND problem document should be returned
        """
        with app_with_decorated_routes.app_context():
            with app_with_decorated_routes.test_client() as client:
                response = client.get(f"/test/has_library/{str(uuid.uuid4())}")
                assert response.status_code == LIBRARY_NOT_FOUND.status_code
                assert response.json["type"] == LIBRARY_NOT_FOUND.uri
                assert response.json["title"] == LIBRARY_NOT_FOUND.title
                assert response.json["status"] == LIBRARY_NOT_FOUND.status_code

    def test_returns_problem_detail(self, app_with_decorated_routes):
        """
        GIVEN: A request to a route which returns a ProblemDetail instance
        WHEN:  The returns_problem_detail decorator intercepts that request
        THEN:  The .response of that ProblemDetail object should be returned
        """
        with app_with_decorated_routes.test_client() as client:
            response = client.get("/test/returns_problem_detail")
            assert response.status_code == PROBLEM_DETAIL_FOR_TEST.status_code
            assert response.json["type"] == PROBLEM_DETAIL_FOR_TEST.uri
            assert response.json["title"] == PROBLEM_DETAIL_FOR_TEST.title
            assert response.json["status"] == PROBLEM_DETAIL_FOR_TEST.status_code

    def test_rjoropd_json(self, app_with_decorated_routes):
        """
        GIVEN: A request to a route that returns a Python object
        WHEN:  The returns_json_or_response_or_problem_detail decorator intercepts
               that request/response
        THEN:  A jsonified version of the object should be available in the response
        """
        with app_with_decorated_routes.test_client() as client:
            response = client.get("/test/rjoropd/json")
            assert response.json == RESPONSE_JSON

    def test_rjoropd_response(self, app_with_decorated_routes):
        """
        GIVEN: A request to a route that returns a Flask Response object
        WHEN:  The returns_json_or_response_or_problem_detail decorator intercepts
               that request/response
        THEN:  That Response should be passed through to the client as-is
        """
        with app_with_decorated_routes.test_client() as client:
            response = client.get("/test/rjoropd/response")
            assert response.data.decode('utf-8') == RESPONSE_OBJ_VAL

    def test_rjoropd_problemdetail(self, app_with_decorated_routes):
        """
        GIVEN: A request to a route that returns a ProblemDetail object
        WHEN:  The returns_json_or_response_or_problem_detail decorator intercepts
               that request/response
        THEN:  The ProblemDetail.response should be returned
        """
        with app_with_decorated_routes.test_client() as client:
            response = client.get("/test/rjoropd/problem_detail")
            assert response.status_code == PROBLEM_DETAIL_FOR_TEST.status_code
            assert response.json["type"] == PROBLEM_DETAIL_FOR_TEST.uri
            assert response.json["title"] == PROBLEM_DETAIL_FOR_TEST.title
            assert response.json["status"] == PROBLEM_DETAIL_FOR_TEST.status_code

    def test_compressible(self, app_with_decorated_routes):
        """
        GIVEN: A response with a known payload
        WHEN:  That response is rendered via the compressible decorator
        THEN:  A gzipped version of that response is returned as the endpoint payload
        """
        buffer = BytesIO()
        with gzip.GzipFile(mode='wb', fileobj=buffer) as gzipped:
            gzipped.write(RESPONSE_OBJ_VAL.encode("utf8"))
        expected = buffer.getvalue()

        with app_with_decorated_routes.test_client() as client:
            response = client.get("/test/compressible",
                                  headers={'Accept-Encoding': 'gzip'})
            assert response.headers['Content-Encoding'] == 'gzip'
            assert response.headers['Vary'] == 'Accept-Encoding'
            assert int(response.headers['Content-Length']) == len(expected)
            assert response.data == expected

    def test_compressible_non_2xx_response(self, app_with_decorated_routes):
        """
        GIVEN: A non-2xx response from a view wrapped by @compressible
        WHEN:  The view function is called
        THEN:  The response should not be compressed
        """
        with app_with_decorated_routes.test_client() as client:
            response = client.get("/test/compressible_4xx",
                                  headers={'Accept-Encoding': 'gzip'})
            assert response.status_code == 400
            assert 'Content-Encoding' not in response.headers.keys()
            assert 'Vary' not in response.headers.keys()

    def test_compressible_already_encoded(self, app_with_decorated_routes):
        """
        GIVEN: A view function which adds a 'Content-Encoding' header to its response
        WHEN:  The view function is called, wrapped by @compressible
        THEN:  The response should not be compressed
        """
        with app_with_decorated_routes.test_client() as client:
            response = client.get(
                "/test/compressible_already_encoded", headers={'Accept-Encoding': 'gzip'})
            assert 199 < response.status_code < 300
            assert 'Vary' not in response.headers.keys()

    def test_check_logged_in_with_jwt(self, app_with_decorated_routes):
        """Test check logged in decorator with JWT token

        Args:
            app_with_decorated_routes (FlaskApp): Flask test enivironment.

        GIVEN:  A valid JWT token in header
        WHEN:   The view function  wrapped by @check_logged_in is called
        THEN:   The RESPONSE_OBJ should be received
        """
        with app_with_decorated_routes.app_context():
            with app_with_decorated_routes.test_client() as client:
                access_token = create_access_token(identity='Admin')
                response = client.get(
                    '/test/check_logged_in', headers={'Authorization': 'Bearer %s' % access_token})
                assert response.data.decode('utf-8') == RESPONSE_OBJ_VAL

    def test_check_logged_in_no_jwt_token(self, app_with_decorated_routes):
        """Test check logged in decorator without JWT token 

        Args:
            app_with_decorated_routes (FlaskApp): Flask test enivironment.

        GIVEN:  A no valid JWT token in header
        WHEN:   The view function  wrapped by @check_logged_in is called
        THEN:   The a 401 UNAUTHORIZED response should be received
        """
        with app_with_decorated_routes.app_context():
            with app_with_decorated_routes.test_client() as client:
                response = client.get(
                    '/test/check_logged_in')
                assert response.status == '401 UNAUTHORIZED'

    def test_after_request_jwt_token_refresh(self, app_with_decorated_routes):
        """Test check logged in decorator without JWT token 

        Args:
            app_with_decorated_routes (FlaskApp): Flask test enivironment.

        GIVEN:  A no valid JWT token in header
        WHEN:   The view function  wrapped by @check_logged_in is called
        THEN:   The a 401 UNAUTHORIZED response should be received
        """
        with app_with_decorated_routes.app_context():
            with app_with_decorated_routes.test_client() as client:
                access_token = create_access_token(
                    identity='Admin', expires_delta=timedelta(minutes=10))
                response = client.get(
                    '/test/check_logged_in', headers={'Authorization': 'Bearer %s' % access_token})
                cookiejar = response.headers.getlist('Set-Cookie')
                print(cookiejar)
                assert response.status == '201 CREATED'
                assert response.data.decode('utf-8') == RESPONSE_OBJ_VAL
