from __future__ import annotations

import base64
import datetime
import json
import random
from contextlib import contextmanager
from smtplib import SMTPException
from urllib.parse import unquote

import flask
import pytest
from Crypto.Cipher import PKCS1_OAEP
from Crypto.PublicKey import RSA
from flask import Flask, Response, session
from werkzeug.datastructures import ImmutableMultiDict, MultiDict

from palace.registry.authentication_document import AuthenticationDocument
from palace.registry.config import Configuration
from palace.registry.controller import (
    AdobeVendorIDController,
    BaseController,
    CoverageController,
    LibraryRegistryAnnotator,
    LibraryRegistryController,
    ValidationController,
)
from palace.registry.emailer import Emailer, EmailTemplate
from palace.registry.opds import OPDSCatalog
from palace.registry.pagination import Pagination
from palace.registry.problem_details import (
    ERROR_RETRIEVING_DOCUMENT,
    INTEGRATION_DOCUMENT_NOT_FOUND,
    INTEGRATION_ERROR,
    INVALID_CREDENTIALS,
    INVALID_INTEGRATION_DOCUMENT,
    LIBRARY_NOT_FOUND,
    NO_AUTH_URL,
    TIMEOUT,
    UNABLE_TO_NOTIFY,
)
from palace.registry.sqlalchemy.model.configuration_setting import ConfigurationSetting
from palace.registry.sqlalchemy.model.delegated_patron_identifier import (
    DelegatedPatronIdentifier,
)
from palace.registry.sqlalchemy.model.external_integration import ExternalIntegration
from palace.registry.sqlalchemy.model.hyperlink import Hyperlink
from palace.registry.sqlalchemy.model.library import Library
from palace.registry.sqlalchemy.model.place import Place
from palace.registry.sqlalchemy.model.resource import Validation
from palace.registry.sqlalchemy.model.service_area import ServiceArea
from palace.registry.sqlalchemy.util import create, get_one, get_one_or_create
from palace.registry.util import GeometryUtility
from palace.registry.util.datetime_helpers import utc_now
from palace.registry.util.file_storage import LibraryLogoStore
from palace.registry.util.http import RequestTimedOut
from palace.registry.util.problem_detail import ProblemDetail
from tests.fixtures.controller import (
    ControllerFixture,
    ControllerSetupFixture,
    MockEmailer,
    MockLibraryRegistry,
)
from tests.fixtures.database import DatabaseTransactionFixture
from tests.testing import DummyHTTPClient


class TestLibraryRegistryAnnotator:
    def test_annotate_catalog(self, controller_setup_fixture: ControllerSetupFixture):
        fixture = controller_setup_fixture.setup()
        annotator = LibraryRegistryAnnotator(fixture.app.library_registry)

        integration, ignore = create(
            fixture.db.session,
            ExternalIntegration,
            protocol=ExternalIntegration.ADOBE_VENDOR_ID,
            goal=ExternalIntegration.DRM_GOAL,
        )
        integration.setting(Configuration.ADOBE_VENDOR_ID).value = "VENDORID"

        with fixture.app.test_request_context("/"):
            catalog = OPDSCatalog(
                fixture.db.session, "Test Catalog", "http://catalog", []
            )
            annotator.annotate_catalog(catalog)

            # The catalog should have three new links: search, register, and a templated link
            # for a library's OPDS entry, in addition to self. It should also have the adobe
            # vendor id in the catalog's metadata.

            links = catalog.catalog.get("links")
            assert len(links) == 4
            [opds_link, register_link, search_link, self_link] = sorted(
                links, key=lambda x: x.get("rel")
            )

            assert opds_link.get("href") == "http://localhost/library/{uuid}"
            assert (
                opds_link.get("rel")
                == "http://librarysimplified.org/rel/registry/library"
            )
            assert opds_link.get("type") == "application/opds+json"
            assert opds_link.get("templated") is True

            assert search_link.get("href") == "http://localhost/search"
            assert search_link.get("rel") == "search"
            assert search_link.get("type") == "application/opensearchdescription+xml"

            assert register_link.get("href") == "http://localhost/register"
            assert register_link.get("rel") == "register"
            assert (
                register_link.get("type")
                == "application/opds+json;profile=https://librarysimplified.org/rel/profile/directory"
            )

            assert catalog.catalog.get("metadata").get("adobe_vendor_id") == "VENDORID"


class TestBaseController:
    def test_library_for_request(
        self, controller_setup_fixture: ControllerSetupFixture
    ):
        with controller_setup_fixture.setup() as fixture:
            # Test the code that looks up a library by its UUID and
            # sets it as flask.request.library.
            controller = BaseController(fixture.library_registry)
            f = controller.library_for_request
            library = fixture.db.library()

            with fixture.app.test_request_context("/"):
                assert f(None) == LIBRARY_NOT_FOUND
                assert f("no such uuid") == LIBRARY_NOT_FOUND

                assert f(library.internal_urn) == library
                assert flask.request.library == library

                flask.request.library = None
                assert f(library.internal_urn[len("urn:uuid:") :]) == library
                assert flask.request.library == library


class TestLibraryRegistry:
    def test_instantiated_controllers(
        self, controller_setup_fixture: ControllerSetupFixture
    ):
        with controller_setup_fixture.setup() as fixture:
            # Verify that the controllers were instantiated and attached
            # to the LibraryRegistry object.
            assert isinstance(
                fixture.library_registry.registry_controller, LibraryRegistryController
            )
            assert isinstance(
                fixture.library_registry.validation_controller, ValidationController
            )

            # No Adobe Vendor ID was set up.
            assert fixture.library_registry.adobe_vendor_id is None

            # Let's configure one.
            fixture.vendor_id_setup()
            registry_with_adobe = MockLibraryRegistry(
                fixture.db.session, testing=True, emailer_class=MockEmailer
            )
            assert isinstance(
                registry_with_adobe.adobe_vendor_id, AdobeVendorIDController
            )


class LibraryRegistryControllerFixture:
    db: DatabaseTransactionFixture
    controller_fixture: ControllerFixture
    controller: LibraryRegistryController
    form: ImmutableMultiDict
    manhattan: str
    oakland: str
    app: Flask
    http_client: DummyHTTPClient

    def __init__(
        self, fixture: ControllerFixture, controller, form, manhattan, oakland
    ):
        self.controller_fixture = fixture
        self.controller = controller
        self.form = form
        self.manhattan = manhattan
        self.oakland = oakland
        self.db = fixture.db
        self.app = self.controller_fixture.app
        self.http_client = self.controller_fixture.http_client

    @contextmanager
    def request_context_with_library(self, route, *args, **kwargs):
        library = kwargs.pop("library")
        with self.app.test_request_context(route, *args, **kwargs) as c:
            flask.request.library = library
            yield c


@pytest.fixture(scope="function")
def registry_controller_fixture(
    controller_setup_fixture: ControllerSetupFixture,
) -> LibraryRegistryControllerFixture:
    def data_setup(fixture: ControllerFixture):
        """Configure the site before setup() creates a LibraryRegistry
        object.
        """
        # Create some places and libraries.
        nypl = fixture.db.nypl  # noqa: F841
        ct_state = fixture.db.connecticut_state_library  # noqa: F841
        ks_state = fixture.db.kansas_state_library  # noqa: F841

        nyc = fixture.db.new_york_city  # noqa: F841
        boston = fixture.db.boston_ma  # noqa: F841
        manhattan_ks = fixture.db.manhattan_ks  # noqa: F841
        us = fixture.db.crude_us  # noqa: F841

        fixture.vendor_id_setup()

    with controller_setup_fixture.setup(data_setup) as fixture:
        controller = LibraryRegistryController(
            fixture.library_registry, emailer_class=MockEmailer
        )

        # A registration form that's valid for most of the tests
        # in this class.
        registration_form = ImmutableMultiDict(
            [
                ("url", "http://circmanager.org/authentication.opds"),
                ("contact", "mailto:integrationproblems@library.org"),
            ]
        )

        # Turn some places into geographic points.
        manhattan = GeometryUtility.point_from_ip("65.88.88.124")
        oakland = GeometryUtility.point_from_string("37.8,-122.2")

        yield LibraryRegistryControllerFixture(
            fixture, controller, registration_form, manhattan, oakland
        )


class TestLibraryRegistryController:
    def _is_library(self, expected, actual, has_email=True):
        # Helper method to check that a library found by a controller is equivalent to a particular library in the database
        flattened = {}
        # Getting rid of the "uuid" key before populating flattened, because its value is just a string, not a subdictionary.
        # The UUID information is still being checked elsewhere.
        del actual["uuid"]
        for subdictionary in list(actual.values()):
            flattened.update(subdictionary)

        for k in flattened:
            if k == "library_stage":
                assert expected._library_stage == flattened.get("library_stage")
            elif k == "timestamp":
                actual_ts = flattened.get("timestamp")
                expected_ts = expected.timestamp
                actual_time = [actual_ts.year, actual_ts.month, actual_ts.day]
                expected_time = [expected_ts.year, expected_ts.month, expected_ts.day]
                assert expected_time == actual_time
            elif k.endswith("_email"):
                if has_email:
                    expected_email = expected.name + "@library.org"
                    assert expected_email == flattened.get(k)
            elif k.endswith("_validated"):
                assert flattened.get(k) == "Not validated"
            elif k == "online_registration":
                assert str(expected.online_registration) == flattened.get(
                    "online_registration"
                )
            elif k in ["focus", "service"]:
                area_type_names = dict(
                    focus=ServiceArea.FOCUS, service=ServiceArea.ELIGIBILITY
                )
                actual_areas = flattened.get(k)
                expected_areas = [
                    x.place.human_friendly_name or "Everywhere"
                    for x in expected.service_areas
                    if x.type == area_type_names[k]
                ]
                assert expected_areas == actual_areas
            elif k == Library.PLS_ID:
                assert expected.pls_id.value == flattened.get(k)
            elif k == "number_of_patrons":
                assert str(getattr(expected, k)) == flattened.get(k)
            elif k in ["help_url"]:
                # Alternate constraint, is not directly part of the library model
                # Is tested in the alternate path
                pass
            else:
                assert getattr(expected, k) == flattened.get(k)

    def _check_keys(self, library):
        # Helper method to check that the controller is sending the right pieces of information about a library.
        expected_categories = [
            "uuid",
            "basic_info",
            "urls_and_contact",
            "stages",
            "areas",
        ]
        assert set(library.keys()) == set(expected_categories)

        expected_info_keys = [
            "name",
            "short_name",
            "description",
            "timestamp",
            "internal_urn",
            "online_registration",
            "pls_id",
            "number_of_patrons",
        ]
        assert set(library.get("basic_info").keys()) == set(expected_info_keys)

        expected_url_contact_keys = [
            "contact_email",
            "help_email",
            "copyright_email",
            "web_url",
            "authentication_url",
            "contact_validated",
            "help_validated",
            "copyright_validated",
            "opds_url",
            "help_url",
        ]
        assert set(library.get("urls_and_contact")) == set(expected_url_contact_keys)

        expected_area_keys = ["focus", "service"]
        assert set(library.get("areas")) == set(expected_area_keys)

        expected_stage_keys = ["library_stage", "registry_stage"]
        assert set(library.get("stages").keys()) == set(expected_stage_keys)

    def test_libraries(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # Test that the controller returns a specific set of information for each library.
        ct = fixture.db.connecticut_state_library
        ks = fixture.db.kansas_state_library
        nypl = fixture.db.nypl

        # Setting this up ensures that patron counts are measured.
        identifier, is_new = DelegatedPatronIdentifier.get_one_or_create(
            fixture.db.session,
            nypl,
            fixture.db.fresh_str(),
            DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID,
            None,
        )

        everywhere = fixture.db.place(type=Place.EVERYWHERE)
        ia = fixture.db.library("InternetArchive", "IA", [everywhere], has_email=True)
        fixture.db.library(
            name="Testing",
            short_name="test_lib",
            library_stage=Library.TESTING_STAGE,
            registry_stage=Library.TESTING_STAGE,
        )

        response = fixture.controller.libraries()
        libraries = response.get("libraries")

        assert len(libraries) == 4
        for library in libraries:
            self._check_keys(library)

        expected_names = [expected.name for expected in [ct, ks, nypl, ia]]
        actual_names = [library.get("basic_info").get("name") for library in libraries]
        assert set(expected_names) == set(actual_names)

        self._is_library(ct, libraries[0])
        self._is_library(ia, libraries[1])
        self._is_library(ks, libraries[2])
        self._is_library(nypl, libraries[3])

    def test_libraries_qa_admin(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # Test that the controller returns a specific set of information for each library.
        ct = fixture.db.connecticut_state_library
        ks = fixture.db.kansas_state_library
        nypl = fixture.db.nypl
        in_testing = fixture.db.library(
            name="Testing",
            short_name="test_lib",
            library_stage=Library.TESTING_STAGE,
            registry_stage=Library.TESTING_STAGE,
        )

        response = fixture.controller.libraries(False)
        libraries = response.get("libraries")

        assert len(libraries) == 4
        for library in libraries:
            self._check_keys(library)

        expected_names = [expected.name for expected in [ct, ks, nypl, in_testing]]
        actual_names = [library.get("basic_info").get("name") for library in libraries]
        assert set(expected_names) == set(actual_names)

        self._is_library(ct, libraries[0])
        self._is_library(ks, libraries[1])
        self._is_library(nypl, libraries[2])
        self._is_library(in_testing, libraries[3], False)

    @pytest.mark.parametrize(
        "production_only, expected_count",
        [
            pytest.param(True, 3, id="production-only"),
            pytest.param(False, 4, id="include-testing"),
        ],
    )
    def test_libraries_opds(
        self,
        production_only: bool,
        expected_count: int,
        registry_controller_fixture: LibraryRegistryControllerFixture,
    ) -> None:
        """Test libraries for the OPDS feed."""
        fixture = registry_controller_fixture

        # Add a library in an overall canceled state.
        fixture.db.library(
            name="Canceled Library",
            short_name="test_canceled_lib",
            library_stage=Library.CANCELLED_STAGE,
            registry_stage=Library.TESTING_STAGE,
            has_email=True,
        )
        # Add a library in an overall testing state.
        fixture.db.library(
            name="My Test Library",
            short_name="my_test_lib",
            library_stage=Library.TESTING_STAGE,
            registry_stage=Library.TESTING_STAGE,
            has_email=True,
        )

        with fixture.app.test_request_context("/libraries"):
            response = fixture.controller.libraries_opds(production_only)

            assert response.status == "200 OK"
            assert response.headers["Content-Type"] == OPDSCatalog.OPDS_TYPE

            catalog = response.json
            catalogs_by_title = {
                lib["metadata"]["title"]: lib for lib in catalog["catalogs"]
            }

            # Verify libraries are sorted alphabetically by title.
            library_titles = [lib["metadata"]["title"] for lib in catalog["catalogs"]]
            assert library_titles == sorted(
                library_titles
            ), f"Libraries not in alphabetical order: {library_titles}"

            assert len(catalog["catalogs"]) == expected_count

            # Canceled libraries are never in OPDS feeds.
            assert "Canceled Library" not in catalogs_by_title

            if production_only:
                assert "My Test Library" not in catalogs_by_title
            else:
                # Include both production and testing libraries.
                assert "My Test Library" in catalogs_by_title

            # These production libraries should always be present in the catalog.
            assert "Connecticut State Library" in catalogs_by_title
            assert "Kansas State Library" in catalogs_by_title
            assert "NYPL" in catalogs_by_title

            # TODO: The following could probably be split off into a separate test
            #  that is more focused on the overall feed (from `catalog_response`).

            # Verify metadata structure - check that at least one library has correct id.
            ct_catalog = catalogs_by_title["Connecticut State Library"]
            assert (
                ct_catalog["metadata"]["id"]
                == fixture.db.connecticut_state_library.internal_urn
            )

            # Verify all expected OPDS links are present.
            link_rels = {link["rel"] for link in catalog["links"]}
            assert "self" in link_rels
            assert "register" in link_rels
            assert "search" in link_rels

            # Verify the self link.
            self_link = next(
                (link for link in catalog["links"] if link["rel"] == "self"), None
            )
            assert self_link is not None
            url_for = fixture.app.library_registry.url_for

            assert self_link["href"] == url_for("libraries_opds")
            assert self_link["rel"] == "self"
            assert self_link["type"] == OPDSCatalog.OPDS_TYPE

    def test_library_details(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # Test that the controller can look up the complete information for one specific library.
        library = fixture.db.nypl

        def check(has_email=True, assertion=True):
            uuid = library.internal_urn.split("uuid:")[1]
            with fixture.app.test_request_context("/"):
                response = fixture.controller.library_details(uuid, 0)
            assert response.get("uuid") == uuid
            self._check_keys(response)
            if assertion:
                self._is_library(library, response, has_email)
            return response

        check()

        # Check if changing the help email to a link removes the email value, and adds a link
        for l in library.hyperlinks:
            if l.rel == Hyperlink.HELP_REL:
                l.href = "http://example.org/help"
        response = check(assertion=False)
        assert response["urls_and_contact"]["help_email"] == None
        assert response["urls_and_contact"]["help_url"] == "http://example.org/help"

        # Delete the library's contact email, simulating an old
        # library created before this rule was instituted, and try
        # again.
        [fixture.db.session.delete(x) for x in library.hyperlinks]
        check(False)

    def test_library_details_with_error(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # Test that the controller returns a problem detail document if the requested library doesn't exist.
        uuid = "not a real UUID!"
        with fixture.app.test_request_context("/"):
            response = fixture.controller.library_details(uuid)

        assert isinstance(response, ProblemDetail)
        assert response.status_code == 404
        assert response.title == LIBRARY_NOT_FOUND.title
        assert response.uri == LIBRARY_NOT_FOUND.uri

    def test_edit_registration(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # Test that a specific library's stages can be edited via submitting a form.
        library = fixture.db.library(
            name="Test Library",
            short_name="test_lib",
            library_stage=Library.CANCELLED_STAGE,
            registry_stage=Library.TESTING_STAGE,
        )
        uuid = library.internal_urn.split("uuid:")[1]
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict(
                [
                    ("uuid", uuid),
                    ("Library Stage", "testing"),
                    ("Registry Stage", "production"),
                ]
            )

            response = fixture.controller.edit_registration()

        assert response._status_code == 200
        assert response.response[0].decode("utf8") == library.internal_urn

        edited_library = get_one(
            fixture.db.session, Library, short_name=library.short_name
        )
        assert edited_library.library_stage == Library.TESTING_STAGE
        assert edited_library.registry_stage == Library.PRODUCTION_STAGE

    def test_edit_registration_with_error(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        uuid = "not a real UUID!"
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict(
                [
                    ("uuid", uuid),
                    ("Library Stage", "testing"),
                    ("Registry Stage", "production"),
                ]
            )
            response = fixture.controller.edit_registration()
        assert isinstance(response, ProblemDetail)
        assert response.status_code == 404
        assert response.title == LIBRARY_NOT_FOUND.title
        assert response.uri == LIBRARY_NOT_FOUND.uri

    def test_edit_registration_with_override(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # Normally, if a library is already in production, its library_stage cannot be edited.
        # Admins should be able to override this by using the interface.
        nypl = fixture.db.nypl
        uuid = nypl.internal_urn.split("uuid:")[1]
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict(
                [
                    ("uuid", uuid),
                    ("Library Stage", "cancelled"),
                    ("Registry Stage", "cancelled"),
                ]
            )

            response = fixture.controller.edit_registration()
            assert response._status_code == 200
            assert response.response[0].decode("utf8") == nypl.internal_urn

    def test_validate_email(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # You can't validate an email for a nonexistent library.
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict(
                [("uuid", "no:such:library"), ("email", "contact_email")]
            )
            response = fixture.controller.validate_email()
        assert isinstance(response, ProblemDetail)
        assert response.status_code == 404
        assert response.title == LIBRARY_NOT_FOUND.title
        assert response.uri == LIBRARY_NOT_FOUND.uri

        nypl = fixture.db.nypl
        uuid = nypl.internal_urn.split("uuid:")[1]
        validation = nypl.hyperlinks[0].resource.validation
        assert validation is None

        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([("uuid", uuid), ("email", "contact_email")])
            fixture.controller.validate_email()

        validation = nypl.hyperlinks[0].resource.validation
        assert isinstance(validation, Validation)
        assert validation.success is True

    def test_missing_email_error(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        library_without_email = fixture.db.library()
        uuid = library_without_email.internal_urn.split("uuid:")[1]
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([("uuid", uuid), ("email", "contact_email")])
            response = fixture.controller.validate_email()

        assert isinstance(response, ProblemDetail)
        assert response.status_code == 400
        assert (
            response.detail == "The contact URI for this library is missing or invalid"
        )
        assert (
            response.uri
            == "http://librarysimplified.org/terms/problem/invalid-contact-uri"
        )

    def test_add_or_edit_pls_id(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # Test that the user can input a new PLS ID
        library = fixture.db.nypl
        assert library.pls_id.value is None
        uuid = library.internal_urn.split("uuid:")[1]
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([("uuid", uuid), ("pls_id", "12345")])
            response = fixture.controller.add_or_edit_pls_id()
        assert response._status_code == 200
        assert response.response[0].decode("utf8") == library.internal_urn

        library_with_pls_id = get_one(
            fixture.db.session, Library, short_name=library.short_name
        )
        assert library_with_pls_id.pls_id.value == "12345"

        # Test that the user can edit an existing PLS ID
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([("uuid", uuid), ("pls_id", "abcde")])
            response = fixture.controller.add_or_edit_pls_id()

        updated = get_one(fixture.db.session, Library, short_name=library.short_name)
        assert updated.pls_id.value == "abcde"

    def test_add_or_edit_pls_id_with_error(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([("uuid", "abc"), ("pls_id", "12345")])
            response = fixture.controller.add_or_edit_pls_id()
        assert response.status_code == 404
        assert response.uri == LIBRARY_NOT_FOUND.uri

    def test_search_details(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        library = fixture.db.nypl
        kansas = fixture.db.kansas_state_library
        connecticut = fixture.db.connecticut_state_library
        with_description = fixture.db.library(
            name="Library With Description",
            has_email=True,
            description="For testing purposes",
        )

        # Searching for the name of a real library returns a dict whose value is a list containing
        # that library.
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict(
                [
                    ("name", "NYPL"),
                ]
            )
            response = fixture.controller.search_details()

        for response_library in response.get("libraries"):
            self._is_library(library, response_library)

        # Searching for part of the library's name--"kansas" instead of "kansas state library" works.
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict(
                [
                    ("name", "kansas"),
                ]
            )
            response = fixture.controller.search_details()

        for response_library in response.get("libraries"):
            self._is_library(kansas, response_library)

        # Searching for a partial name may yield multiple results.
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict(
                [
                    ("name", "state"),
                ]
            )
            response = fixture.controller.search_details()
        libraries = response.get("libraries")
        assert len(libraries) == 2
        libraries.sort(key=lambda library: library["basic_info"]["name"])
        self._is_library(connecticut, libraries[0])
        self._is_library(kansas, libraries[1])

        # Searching for a word or phrase found within a library's description returns a dict whose value is a list containing
        # that library.
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([("name", "testing")])
            response = fixture.controller.search_details()
        self._is_library(with_description, response.get("libraries")[0])

        # Searching for a name that cannot be found returns a problem detail.
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict(
                [
                    ("name", "other"),
                ]
            )
            response = fixture.controller.search_details()

        assert response == LIBRARY_NOT_FOUND

    def _log_in(self, registry_controller_fixture: LibraryRegistryControllerFixture):
        flask.request.form = MultiDict(
            [
                ("username", "Admin"),
                ("password", "123"),
            ]
        )
        return registry_controller_fixture.controller.log_in()

    def test_log_in(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        with fixture.app.test_request_context("/", method="POST"):
            response = self._log_in(fixture)
            assert response.status == "302 FOUND"
            assert session["username"] == "Admin"

    def test_log_in_with_error(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        fixture.db.admin()
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict(
                [
                    ("username", "Admin"),
                    ("password", "wrong"),
                ]
            )
            response = fixture.controller.log_in()
            assert isinstance(response, ProblemDetail)
            assert response.status_code == 401
            assert response.title == INVALID_CREDENTIALS.title
            assert response.uri == INVALID_CREDENTIALS.uri

    def test_log_in_new_admin(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict(
                [("username", "New"), ("password", "password")]
            )
            response = fixture.controller.log_in()
            assert response.status == "302 FOUND"
            assert session["username"] == "New"

    def test_log_out(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        with fixture.app.test_request_context("/"):
            self._log_in(fixture)
            assert session["username"] == "Admin"
            response = fixture.controller.log_out()
            assert session["username"] == ""
            assert response.status == "302 FOUND"

    def test_instantiate_without_emailer(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture
        """If there is no emailer configured, the controller will still start
        up.
        """
        controller = LibraryRegistryController(
            fixture.controller_fixture.library_registry
        )
        assert controller.emailer is None

    def test_nearby(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        with fixture.app.test_request_context("/"):
            response = fixture.controller.nearby(fixture.manhattan, live=True)
            assert isinstance(response, Response)
            assert "200 OK" == response.status
            assert response.headers["Content-Type"] == OPDSCatalog.OPDS_TYPE
            catalog = json.loads(response.data)

            # The catalog can be cached for a while, since the list of libraries
            # doesn't change very quickly.
            assert (
                response.headers["Cache-Control"]
                == "public, no-transform, max-age: 43200, s-maxage: 21600"
            )

            # We found both libraries within a 150-kilometer radius of the
            # starting point.
            nypl, ct = catalog["catalogs"]
            assert nypl["metadata"]["title"] == "NYPL"
            assert nypl["metadata"]["distance"] == "0 km."
            assert ct["metadata"]["title"] == "Connecticut State Library"
            assert ct["metadata"]["distance"] == "29 km."

            # If that's not good enough, there's a link to the search
            # controller, so you can do a search.
            [library_link, register_link, search_link, self_link] = sorted(
                catalog["links"], key=lambda x: x["rel"]
            )
            url_for = fixture.app.library_registry.url_for

            assert self_link["href"] == url_for("nearby")
            assert self_link["rel"] == "self"
            assert self_link["type"] == OPDSCatalog.OPDS_TYPE

            assert search_link["href"] == url_for("search")
            assert search_link["rel"] == "search"
            assert search_link["type"] == "application/opensearchdescription+xml"

            assert register_link["href"] == url_for("register")
            assert register_link["rel"] == "register"
            assert (
                register_link["type"]
                == "application/opds+json;profile=https://librarysimplified.org/rel/profile/directory"
            )

            assert library_link["href"] == unquote(url_for("library", uuid="{uuid}"))
            assert (
                library_link["rel"]
                == "http://librarysimplified.org/rel/registry/library"
            )
            assert library_link["type"] == "application/opds+json"
            assert library_link.get("templated") is True

            assert catalog["metadata"]["adobe_vendor_id"] == "VENDORID"

    def test_nearby_qa(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # The libraries we used in the previous test are in production.
        # If we move them from production to TESTING, we won't find anything.
        for library in fixture.db.session.query(Library):
            library.registry_stage = Library.TESTING_STAGE
        with fixture.app.test_request_context("/"):
            response = fixture.controller.nearby(fixture.manhattan, live=True)
            catalogs = json.loads(response.data)
            assert catalogs["catalogs"] == []

        # However, they will show up in the QA feed.
        with fixture.app.test_request_context("/"):
            response = fixture.controller.nearby(fixture.manhattan, live=False)
            catalogs = json.loads(response.data)
            assert len(catalogs["catalogs"]) == 2
            [catalog] = [
                x
                for x in catalogs["catalogs"]
                if x["metadata"]["id"] == fixture.db.nypl.internal_urn
            ]
            assert catalog["metadata"]["title"] == "NYPL"

            # Some of the links are the same as in the production feed;
            # others are different.
            url_for = fixture.app.library_registry.url_for
            [library_link, register_link, search_link, self_link] = sorted(
                catalogs["links"], key=lambda x: x["rel"]
            )

            # The 'register' link is the same as in the main feed.
            assert register_link["href"] == url_for("register")
            assert register_link["rel"] == "register"

            # So is the 'library' templated link.
            assert library_link["href"] == unquote(url_for("library", uuid="{uuid}"))
            assert (
                library_link["rel"]
                == "http://librarysimplified.org/rel/registry/library"
            )

            # This is a QA feed, and the 'search' and 'self' links
            # will give results from the QA feed.
            assert self_link["href"] == url_for("nearby_qa")
            assert self_link["rel"] == "self"

            assert search_link["href"] == url_for("search_qa")
            assert search_link["rel"] == "search"

    def test_nearby_no_location(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        with fixture.app.test_request_context("/"):
            response = fixture.controller.nearby(None)
            assert isinstance(response, Response)
            assert response.status == "200 OK"
            assert response.headers["Content-Type"] == OPDSCatalog.OPDS_TYPE
            catalogs = json.loads(response.data)

            # We found no nearby libraries, because we had no location to
            # start with.
            assert catalogs["catalogs"] == []

    def test_nearby_no_libraries(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        with fixture.app.test_request_context("/"):
            response = fixture.controller.nearby(fixture.oakland)
            assert isinstance(response, Response)
            assert response.status == "200 OK"
            assert response.headers["Content-Type"] == OPDSCatalog.OPDS_TYPE
            catalog = json.loads(response.data)

            # We found no nearby libraries, because we were across the
            # country from the only ones in the registry.
            assert catalog["catalogs"] == []

    def test_search_form(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        with fixture.app.test_request_context("/"):
            response = fixture.controller.search(None)
            assert response.status == "200 OK"
            assert (
                response.headers["Content-Type"]
                == "application/opensearchdescription+xml"
            )

            # The search form can be cached more or less indefinitely.
            assert (
                response.headers["Cache-Control"]
                == "public, no-transform, max-age: 2592000"
            )

            # The search form points the client to the search controller.
            expect_url = fixture.app.library_registry.url_for("search")
            expect_url_tag = (
                '<Url type="application/atom+xml;profile=opds-catalog" template="%s?q={searchTerms}"/>'
                % expect_url
            )
            assert expect_url_tag in response.data.decode("utf8")

    def test_qa_search_form(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        """The QA search form links to the QA search controller."""
        with fixture.app.test_request_context("/"):
            response = fixture.controller.search(None, live=False)
            assert response.status == "200 OK"

            expect_url = fixture.app.library_registry.url_for("search_qa")
            expect_url_tag = (
                '<Url type="application/atom+xml;profile=opds-catalog" template="%s?q={searchTerms}"/>'
                % expect_url
            )
            assert expect_url_tag in response.data.decode("utf8")

    def test_search(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        with fixture.app.test_request_context("/?q=manhattan"):
            response = fixture.controller.search(fixture.manhattan)
            assert response.status == "200 OK"
            assert response.headers["Content-Type"] == OPDSCatalog.OPDS_TYPE
            catalog = json.loads(response.data)
            # We found the two matching results.
            [nypl, ks] = catalog["catalogs"]
            assert nypl["metadata"]["title"] == "NYPL"
            assert nypl["metadata"]["distance"] == "0 km."

            assert ks["metadata"]["title"] == "Kansas State Library"
            assert ks["metadata"]["distance"] == "1928 km."

            [library_link, register_link, search_link, self_link] = sorted(
                catalog["links"], key=lambda x: x["rel"]
            )
            url_for = fixture.app.library_registry.url_for

            # The search results have a self link and a link back to
            # the search form.
            assert self_link["href"] == url_for("search", q="manhattan")
            assert self_link["rel"] == "self"
            assert self_link["type"] == OPDSCatalog.OPDS_TYPE

            assert search_link["href"] == url_for("search")
            assert search_link["rel"] == "search"
            assert search_link["type"] == "application/opensearchdescription+xml"

            assert register_link["href"] == url_for("register")
            assert register_link["rel"] == "register"
            assert (
                register_link["type"]
                == "application/opds+json;profile=https://librarysimplified.org/rel/profile/directory"
            )

            assert library_link["href"] == unquote(url_for("library", uuid="{uuid}"))
            assert (
                library_link["rel"]
                == "http://librarysimplified.org/rel/registry/library"
            )
            assert library_link["type"] == "application/opds+json"
            assert library_link.get("templated") is True

            assert catalog["metadata"]["adobe_vendor_id"] == "VENDORID"

    def test_search_qa(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # As we saw in the previous test, this search picks up two
        # libraries when we run it looking for production libraries. If
        # all of the libraries are cancelled, we don't find anything.
        for library in fixture.db.session.query(Library):
            assert library.registry_stage == Library.PRODUCTION_STAGE

        for library in fixture.db.session.query(Library):
            library.registry_stage = Library.CANCELLED_STAGE
        with fixture.app.test_request_context("/?q=manhattan"):
            response = fixture.controller.search(fixture.manhattan, live=True)
            catalog = json.loads(response.data)
            assert catalog["catalogs"] == []

        # If we move one of the libraries back into the PRODUCTION
        # stage, we find it.
        fixture.db.kansas_state_library.registry_stage = Library.PRODUCTION_STAGE
        with fixture.app.test_request_context("/?q=manhattan"):
            response = fixture.controller.search(fixture.manhattan, live=True)
            catalog = json.loads(response.data)
            [catalog] = catalog["catalogs"]
            assert catalog["metadata"]["title"] == "Kansas State Library"

    def test_library(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        nypl = fixture.db.nypl
        with fixture.request_context_with_library("/", library=nypl):
            response = fixture.controller.library()
        [catalog_entry] = json.loads(response.data).get("catalogs")
        assert catalog_entry.get("metadata").get("title") == nypl.name
        assert catalog_entry.get("metadata").get("id") == nypl.internal_urn

    def queue_opds_success(
        self,
        registry_controller_fixture: LibraryRegistryControllerFixture,
        auth_url="http://circmanager.org/authentication.opds",
        media_type=None,
    ):
        """The next HTTP request made by the registry will appear to retrieve
        a functional OPDS feed that links to `auth_url` as its
        Authentication For OPDS document.
        """
        media_type = media_type or OPDSCatalog.OPDS_1_TYPE
        registry_controller_fixture.http_client.queue_response(
            200,
            media_type,
            links={
                AuthenticationDocument.AUTHENTICATION_DOCUMENT_REL: {
                    "url": auth_url,
                    "rel": AuthenticationDocument.AUTHENTICATION_DOCUMENT_REL,
                }
            },
        )

    def _auth_document(self, key=None):
        auth_document = {
            "id": "http://circmanager.org/authentication.opds",
            "title": "A Library",
            "service_description": "Description",
            "authentication": [
                {"type": "https://librarysimplified.org/rel/auth/anonymous"}
            ],
            "links": [
                {
                    "rel": "alternate",
                    "href": "http://circmanager.org",
                    "type": "text/html",
                },
                {"rel": "logo", "href": "data:image/png;imagedata"},
                {"rel": "register", "href": "http://circmanager.org/new-account"},
                {
                    "rel": "start",
                    "href": "http://circmanager.org/feed/",
                    "type": "application/atom+xml;profile=opds-catalog",
                },
                {"rel": "help", "href": "http://help.library.org/"},
                {"rel": "help", "href": "mailto:help@library.org"},
                {
                    "rel": "http://librarysimplified.org/rel/designated-agent/copyright",
                    "href": "mailto:dmca@library.org",
                },
            ],
            "service_area": {"US": "Kansas"},
            "collection_size": 100,
        }

        if key:
            auth_document["public_key"] = {
                "type": "RSA",
                "value": key.publickey().exportKey().decode("utf8"),
            }
        return auth_document

    def test_register_get(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # When there is no terms-of-service document, you can get a
        # document describing the authentication process but it's
        # empty.
        with fixture.app.test_request_context("/", method="GET"):
            response = fixture.controller.register()
            assert response.status_code == 200
            assert json.loads(response.data) == {}

        # Set a terms-of-service link.
        tos = "http://terms.com/service.html"
        ConfigurationSetting.sitewide(
            fixture.db.session, Configuration.REGISTRATION_TERMS_OF_SERVICE_URL
        ).value = tos

        # And a terms-of-service HTML snippet.
        html = 'Terms of service are <a href="http://terms.com/service.html">over here</a>.'
        ConfigurationSetting.sitewide(
            fixture.db.session, Configuration.REGISTRATION_TERMS_OF_SERVICE_HTML
        ).value = html

        # Now the document contains two links, both with the
        # 'terms-of-service' rel. One links to the terms of service
        # document, the other is a data: URI containing a snippet of
        # HTML.
        with fixture.app.test_request_context("/", method="GET"):
            response = fixture.controller.register()
            assert response.status_code == 200
            data = json.loads(response.data)

            # Both links have the same rel and type.
            for link in data["links"]:
                assert link["rel"] == "terms-of-service"
                assert link["type"] == "text/html"

            # Verifying the http: link is simple.
            [http_link, data_link] = data["links"]
            assert http_link["href"] == tos

            # To verify the data: link we must first separate it from its
            # header and decode it.
            header, encoded = data_link["href"].split(",", 1)
            assert header == "data:text/html;base64"

            decoded = base64.b64decode(encoded)
            assert decoded.decode("utf8") == html

    def test_register_fails_when_no_auth_document_url_provided(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture
        """Without the URL to an Authentication For OPDS document,
        the registration process can't begin.
        """
        with fixture.app.test_request_context("/", method="POST"):
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response == NO_AUTH_URL

    def test_register_fails_when_auth_document_url_times_out(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            fixture.http_client.queue_response(RequestTimedOut("http://url", "sorry"))
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == TIMEOUT.uri
            assert (
                response.detail
                == "Timeout retrieving auth document http://circmanager.org/authentication.opds"
            )

    def test_register_fails_on_non_200_code(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        """If the URL provided results in a status code other than
        200, the registration process can't begin.
        """
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form

            # This server isn't working.
            fixture.http_client.queue_response(500)
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == ERROR_RETRIEVING_DOCUMENT.uri
            assert (
                response.detail
                == "Error retrieving auth document http://circmanager.org/authentication.opds"
            )

            # This server incorrectly requires authentication to
            # access the authentication document.
            fixture.http_client.queue_response(401)
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == ERROR_RETRIEVING_DOCUMENT.uri
            assert (
                response.detail
                == "Error retrieving auth document http://circmanager.org/authentication.opds"
            )

            # This server doesn't have an authentication document
            # at the specified URL.
            fixture.http_client.queue_response(404)
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == INTEGRATION_DOCUMENT_NOT_FOUND.uri
            assert (
                response.detail
                == "No Authentication For OPDS document present at http://circmanager.org/authentication.opds"
            )

    def test_register_fails_on_non_authentication_document(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # The request succeeds but returns something other than
        # an authentication document.
        fixture.http_client.queue_response(
            200, content="I am not an Authentication For OPDS document."
        )
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response == INVALID_INTEGRATION_DOCUMENT

    def test_register_fails_on_non_matching_id(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # The request returns an authentication document but its `id`
        # doesn't match the final URL it was retrieved from.
        auth_document = self._auth_document()
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url="http://a-different-url/"
        )
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict(
                [
                    ("url", "http://a-different-url/"),
                    ("contact", "mailto:me@library.org"),
                ]
            )
            response = fixture.controller.register(do_get=fixture.http_client.do_get)

            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert (
                response.detail
                == "The OPDS authentication document's id (http://circmanager.org/authentication.opds) doesn't match its url (http://a-different-url/)."
            )

    def test_register_fails_on_missing_title(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # The request returns an authentication document but it's missing
        # a title.
        auth_document = self._auth_document()
        del auth_document["title"]
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document["id"]
        )
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert (
                response.detail
                == "The OPDS authentication document is missing a title."
            )

    def test_register_fails_on_no_start_link(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # The request returns an authentication document but it's missing
        # a link to an OPDS feed.
        auth_document = self._auth_document()
        for link in list(auth_document["links"]):
            if link["rel"] == "start":
                auth_document["links"].remove(link)
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document["id"]
        )
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert (
                response.detail
                == "The OPDS authentication document is missing a 'start' link to the root OPDS feed."
            )

    def test_register_fails_on_start_link_not_found(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # The request returns an authentication document but an attempt
        # to retrieve the corresponding OPDS feed yields a 404.
        auth_document = self._auth_document()
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document["id"]
        )
        fixture.http_client.queue_response(404)
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == INTEGRATION_DOCUMENT_NOT_FOUND.uri
            assert (
                response.detail
                == "No OPDS root document present at http://circmanager.org/feed/"
            )

    def test_register_fails_on_start_link_timeout(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # The request returns an authentication document but an attempt
        # to retrieve the corresponding OPDS feed times out.
        auth_document = self._auth_document()
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document["id"]
        )
        fixture.http_client.queue_response(RequestTimedOut("http://url", "sorry"))
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == TIMEOUT.uri
            assert (
                response.detail
                == "Timeout retrieving OPDS root document at http://circmanager.org/feed/"
            )

    def test_register_fails_on_start_link_error(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # The request returns an authentication document but an attempt
        # to retrieve the corresponding OPDS feed gives a server-side error.
        auth_document = self._auth_document()
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document["id"]
        )
        fixture.http_client.queue_response(500)
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == ERROR_RETRIEVING_DOCUMENT.uri
            assert (
                response.detail
                == "Error retrieving OPDS root document at http://circmanager.org/feed/"
            )

    @pytest.mark.parametrize(
        "media_type, expect_success",
        [
            pytest.param(
                "application/atom+xml;profile=opds-catalog;kind=acquisition",
                True,
                id="opds1-acquisition",
            ),
            pytest.param(
                "application/atom+xml;kind=acquisition;profile=opds-catalog",
                True,
                id="opds1_acquisition-different-order",
            ),
            pytest.param(
                "application/atom+xml;profile=opds-catalog;kind=acquisition;api-version=1",
                True,
                id="opds1-acquisition-apiv1",
            ),
            pytest.param(
                "application/atom+xml;api-version=1;kind=acquisition;profile=opds-catalog",
                True,
                id="opds1_acquisition_apiv1-different-order",
            ),
            pytest.param(
                "application/atom+xml;api-version=2;kind=acquisition;profile=opds-catalog",
                True,
                id="opds1-acquisition-apiv2",
            ),
            pytest.param(
                "application/atom+xml;profile=opds-catalog;kind=navigation",
                False,
                id="opds1-navigation",
            ),
            pytest.param("application/opds+json;api-version=1", True, id="opds2-apiv1"),
            pytest.param("application/opds+json;api-version=2", True, id="opds2-apiv2"),
            pytest.param("application/epub+zip", False, id="epub+zip"),
            pytest.param("application/json", False, id="application-json"),
            pytest.param("", False, id="empty-string"),
            pytest.param(None, False, id="none-value"),
        ],
    )
    def test_register_fails_on_start_link_not_opds_feed(
        self,
        media_type: str | None,
        expect_success: bool,
        registry_controller_fixture: LibraryRegistryControllerFixture,
    ) -> None:
        fixture = registry_controller_fixture
        # An empty string media type results in a no content-type header.
        content_type = None if media_type == "" else media_type

        """The request returns an authentication document but an attempt
        to retrieve the corresponding OPDS feed gives a server-side error.
        """
        auth_document = self._auth_document()
        # The start link returns a 200 response code but the media type might be wrong.
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document["id"]
        )

        fixture.http_client.queue_response(200, media_type)
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            # We expect to get INVALID_INTEGRATION_DOCUMENT problem detail here, in any case,
            # since our test is not fully configured.
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            # But we should see the `not OPDS` detail only in the case of an invalid media type.
            assert (
                response.detail
                != f"Supposed root document at http://circmanager.org/feed/ does not appear to be an OPDS document (content_type={content_type!r})."
            ) == expect_success

    def test_register_fails_if_start_link_does_not_link_back_to_auth_document(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture
        auth_document = self._auth_document()
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document["id"]
        )

        # The start link returns a 200 response code and the right
        # Content-Type, but there is no Link header and the body is no
        # help.
        fixture.http_client.queue_response(200, OPDSCatalog.OPDS_TYPE, content="{}")
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert (
                response.detail
                == "OPDS root document at http://circmanager.org/feed/ does not link back to authentication document http://circmanager.org/authentication.opds"
            )

    def test_register_fails_on_broken_logo_link(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture
        """The request returns a valid authentication document
        that links to a broken logo image.
        """
        auth_document = self._auth_document()
        for link in auth_document["links"]:
            if link["rel"] == "logo":
                link["href"] = "http://example.com/broken-logo.png"
                break
        # Auth document request succeeds.
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document["id"]
        )

        # OPDS feed request succeeds.
        self.queue_opds_success(fixture)

        # Image request fails.
        fixture.http_client.queue_response(500)

        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert (
                response.detail
                == "Could not read logo image http://example.com/broken-logo.png"
            )

    def test_register_fails_on_unknown_service_area(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        """The auth document is valid but the registry doesn't recognize the
        library's service area.
        """
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            auth_document = self._auth_document()
            auth_document["service_area"] = {"US": ["Somewhere"]}
            fixture.http_client.queue_response(
                200, content=json.dumps(auth_document), url=auth_document["id"]
            )
            self.queue_opds_success(fixture)
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert (
                response.detail
                == 'The following service area was unknown: {"US": ["Somewhere"]}.'
            )

    def test_register_fails_on_ambiguous_service_area(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # Create a situation (which shouldn't exist in real life)
        # where there are two places with the same name and the same
        # .parent.
        fixture.db.new_york_city.parent = fixture.db.crude_us
        fixture.db.manhattan_ks.parent = fixture.db.crude_us

        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            auth_document = self._auth_document()
            auth_document["service_area"] = {"US": ["Manhattan"]}
            fixture.http_client.queue_response(
                200, content=json.dumps(auth_document), url=auth_document["id"]
            )
            self.queue_opds_success(fixture)
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert (
                response.detail
                == 'The following service area was ambiguous: {"US": ["Manhattan"]}.'
            )

    def test_register_fails_on_401_with_no_authentication_document(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            auth_document = self._auth_document()
            fixture.http_client.queue_response(
                200, content=json.dumps(auth_document), url=auth_document["id"]
            )
            fixture.http_client.queue_response(401)
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert (
                response.detail
                == "401 response at http://circmanager.org/feed/ did not yield an Authentication For OPDS document"
            )

    def test_register_fails_on_401_if_authentication_document_ids_do_not_match(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            auth_document = self._auth_document()
            fixture.http_client.queue_response(
                200, content=json.dumps(auth_document), url=auth_document["id"]
            )
            auth_document["id"] = "http://some-other-id/"
            fixture.http_client.queue_response(
                401,
                AuthenticationDocument.MEDIA_TYPE,
                content=json.dumps(auth_document),
                url=auth_document["id"],
            )

            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.uri == INVALID_INTEGRATION_DOCUMENT.uri
            assert (
                response.detail
                == "Authentication For OPDS document guarding http://circmanager.org/feed/ does not match the one at http://circmanager.org/authentication.opds"
            )

    def test_register_succeeds_on_401_if_authentication_document_ids_match(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            auth_document = self._auth_document()
            fixture.http_client.queue_response(
                200, content=json.dumps(auth_document), url=auth_document["id"]
            )
            fixture.http_client.queue_response(
                401,
                AuthenticationDocument.MEDIA_TYPE,
                content=json.dumps(auth_document),
                url=auth_document["id"],
            )

            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.status_code == 201

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
    #         assert response.title == "Invalid or missing configuration contact email address"

    #         flask.request.form = ImmutableMultiDict([
    #             ("url", "http://circmanager.org/authentication.opds"),
    #             ("contact", "http://contact-us/")
    #         ])
    #         response = self.controller.register(do_get=self.http_client.do_get)
    #         assert response.title == "Invalid or missing configuration contact email address"

    def test_register_fails_on_missing_email_in_authentication_document(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        for rel, error, badlink in (
            (
                "http://librarysimplified.org/rel/designated-agent/copyright",
                "Invalid or missing copyright designated agent email address",
                "http://not-an-email/",
            ),
            (
                "help",
                "Invalid or missing patron support email address or website",
                "tcp://not-an-email-or-site/",
            ),
        ):
            # Start with a valid document.
            auth_document = self._auth_document()

            # Remove the crucial link.
            auth_document["links"] = [
                x for x in auth_document["links"] if x["rel"] != rel
            ]

            def _request_fails():
                fixture.http_client.queue_response(
                    200, content=json.dumps(auth_document), url=auth_document["id"]
                )
                with fixture.app.test_request_context("/", method="POST"):
                    flask.request.form = fixture.form
                    response = fixture.controller.register(
                        do_get=fixture.http_client.do_get
                    )
                    assert response.title == error

            _request_fails()

            # Now add the link back but as an http: link.
            auth_document["links"].append(dict(rel=rel, href=badlink))
            _request_fails()

    def test_registration_with_only_patron_support_site(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        """Test the register() without a mailto: HELP rel but an http:// site"""
        auth_document = self._auth_document()
        auth_document["links"] = list(
            filter(
                lambda x: x["href"] != "mailto:help@library.org", auth_document["links"]
            )
        )

        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document["id"]
        )
        self.queue_opds_success(fixture)
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = fixture.form
            response = fixture.controller.register(do_get=fixture.http_client.do_get)

        assert response.status_code == 201
        for link in response.json["links"]:
            if link.get("rel") == "help" and link["href"] == "http://help.library.org/":
                break
        else:
            assert (
                False
            ), "Did not find the help link 'http://help.library.org/' in the response"

    def test_registration_fails_if_email_server_fails(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        """Even if everything looks good, registration can fail if
        the library registry can't send out the validation emails.
        """

        # Simulate an SMTP server that won't accept email for
        # whatever reason.
        class NonfunctionalEmailer(MockEmailer):
            def send(self, *args, **kwargs):
                raise SMTPException("SMTP server is broken")

        fixture.controller.emailer = NonfunctionalEmailer()

        # Pretend we are a library with a valid authentication document.
        auth_document = self._auth_document(None)
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document["id"]
        )
        self.queue_opds_success(fixture)

        # Send a registration request to the registry.
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict(
                [
                    ("url", auth_document["id"]),
                    ("contact", "mailto:me@library.org"),
                ]
            )
            response = fixture.controller.register(do_get=fixture.http_client.do_get)

        # We get back a ProblemDetail the first time
        # we got a problem sending an email. In this case, it was
        # trying to contact the library's 'help' address included in the
        # library's authentication document.
        assert response.uri == INTEGRATION_ERROR.uri
        assert (
            response.detail
            == "SMTP error while sending email to mailto:dmca@library.org"
        )

    def test_registration_fails_if_email_server_unusable(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        """
        GIVEN: An email integration which is missing or not responding
        WHEN:  A registration is requested
        THEN:  A ProblemDetail of an appropriate type should be returned
        """

        # Simulate an SMTP server that is wholly unresponsive
        class UnresponsiveEmailer(Emailer):
            def _send_email(*args):
                raise Exception("message from UnresponsiveEmailer")

        unresponsive_emailer_kwargs = {
            "smtp_username": "library",
            "smtp_password": "library",
            "smtp_host": "library",
            "smtp_port": "12345",
            "from_name": "Test",
            "from_address": "test@library.tld",
            "templates": {
                "address_needs_confirmation": EmailTemplate(
                    "subject", "Hello, %(to_address)s, this is %(from_address)s."
                )
            },
        }
        fixture.controller.emailer = UnresponsiveEmailer(**unresponsive_emailer_kwargs)

        # Pretend we are a library with a valid authentication document.
        auth_document = self._auth_document(None)
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document["id"]
        )
        self.queue_opds_success(fixture)

        # Send a registration request to the registry.
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict(
                [
                    ("url", auth_document["id"]),
                    ("contact", "mailto:me@library.org"),
                ]
            )
            response = fixture.controller.register(do_get=fixture.http_client.do_get)

        # We get back a ProblemDetail the first time
        # we got a problem sending an email. In this case, it was
        # trying to contact the library's 'help' address included in the
        # library's authentication document.
        assert response.uri == UNABLE_TO_NOTIFY.uri

    # TODO: This test is very, very slow (on trhe order of 30s locally).
    def test_register_success(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        opds_directory = "application/opds+json;profile=https://librarysimplified.org/rel/profile/directory"

        # Pretend we are a library with a valid authentication document.
        key = RSA.generate(1024)
        auth_document = self._auth_document(key)
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document["id"]
        )
        self.queue_opds_success(fixture)

        auth_url = "http://circmanager.org/authentication.opds"
        opds_url = "http://circmanager.org/feed/"

        # Send a registration request to the registry.
        random.seed(42)
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict(
                [
                    ("url", auth_url),
                    ("contact", "mailto:me@library.org"),
                ]
            )
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.status_code == 201
            assert response.headers.get("Content-Type") == opds_directory

            # The library has been created. Information from its
            # authentication document has been added to the database.
            library = get_one(fixture.db.session, Library, opds_url=opds_url)
            assert library != None
            assert library.name == "A Library"
            assert library.description == "Description"
            assert library.web_url == "http://circmanager.org"

            # The client didn't specify a stage, so the server acted
            # like the client asked to be put into production.
            assert library.library_stage == Library.PRODUCTION_STAGE

            assert library.anonymous_access is True
            assert library.online_registration is True

            [collection_summary] = library.collections
            assert collection_summary.language is None
            assert collection_summary.size == 100
            [service_area] = library.service_areas
            assert service_area.place_id == fixture.db.kansas_state.id

            # To get this information, a request was made to the
            # circulation manager's Authentication For OPDS document.
            # A follow-up request was made to the feed mentioned in that
            # document.
            #
            assert fixture.http_client.requests == [
                "http://circmanager.org/authentication.opds",
                "http://circmanager.org/feed/",
            ]

            # And the document we queued up was fed into the library
            # registry.
            catalog = json.loads(response.data)
            assert catalog["metadata"]["title"] == "A Library"
            assert catalog["metadata"]["description"] == "Description"

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
            expect = "UDAXIH"
            assert expect == library.short_name
            assert len(library.shared_secret) == 48

            assert catalog["metadata"]["short_name"] == library.short_name
            # The registry encrypted the secret with the public key, and
            # it can be decrypted with the private key.
            encryptor = PKCS1_OAEP.new(key)
            shared_secret = catalog["metadata"]["shared_secret"]
            encrypted_secret = base64.b64decode(shared_secret.encode("utf8"))
            decrypted_secret = encryptor.decrypt(encrypted_secret)
            assert decrypted_secret.decode("utf8") == library.shared_secret

        old_secret = library.shared_secret
        fixture.http_client.requests = []

        # Hyperlink objects were created for the three email addresses
        # associated with the library.
        help_link, copyright_agent_link, integration_contact_link = sorted(
            library.hyperlinks, key=lambda x: x.rel
        )
        assert help_link.rel == "help"
        assert (
            help_link.href == "http://help.library.org/"
        )  # The first valid link is now the website
        assert copyright_agent_link.rel == Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL
        assert copyright_agent_link.href == "mailto:dmca@library.org"
        assert integration_contact_link.rel == Hyperlink.INTEGRATION_CONTACT_REL
        assert integration_contact_link.href == "mailto:me@library.org"

        # A confirmation email was sent out for each of those addresses.
        sent = sorted(fixture.controller.emailer.sent_out, key=lambda x: x[1])
        for email in sent:
            assert email[0] == Emailer.ADDRESS_NEEDS_CONFIRMATION
        destinations = [x[1] for x in sent]
        assert destinations == [
            "dmca@library.org",
            "me@library.org",
        ]
        fixture.controller.emailer.sent_out = []

        # The document sent by the library registry to the library
        # includes status information about the library's integration
        # contact address -- information that wouldn't be made
        # available to the public.
        [link] = [
            x
            for x in catalog["links"]
            if x.get("rel") == Hyperlink.INTEGRATION_CONTACT_REL
        ]
        assert link["href"] == "mailto:me@library.org"
        assert link["properties"][Validation.STATUS_PROPERTY] == Validation.IN_PROGRESS

        # Later, the library's information changes.
        auth_document = {
            "id": auth_url,
            "name": "A Library",
            "service_description": "New and improved",
            "links": [
                {"rel": "logo", "href": "/logo.png", "type": "image/png"},
                {
                    "rel": "start",
                    "href": "http://circmanager.org/feed/",
                    "type": "application/atom+xml;profile=opds-catalog",
                },
                {"rel": "help", "href": "mailto:new-help@library.org"},
                {
                    "rel": "http://librarysimplified.org/rel/designated-agent/copyright",
                    "href": "mailto:me@library.org",
                },
            ],
            "service_area": {"US": "Connecticut"},
        }
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=auth_document["id"]
        )
        self.queue_opds_success(fixture)

        # We have a new logo as well.
        image_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x01\x03\x00\x00\x00%\xdbV\xca\x00\x00\x00\x06PLTE\xffM\x00\x01\x01\x01\x8e\x1e\xe5\x1b\x00\x00\x00\x01tRNS\xcc\xd24V\xfd\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
        fixture.http_client.queue_response(
            200, content=image_data, media_type="image/png"
        )

        # So the library re-registers itself, and gets an updated
        # registry entry.
        #
        # This time, the library explicitly specifies which stage it
        # wants to be in.
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.form = ImmutableMultiDict(
                [
                    ("url", auth_url),
                    ("contact", "mailto:me@library.org"),
                    ("stage", Library.TESTING_STAGE),
                ]
            )

            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.status_code == 200
            assert response.headers.get("Content-Type") == opds_directory

            # The data sent in the response includes the library's new
            # data.
            catalog = json.loads(response.data)
            assert catalog["metadata"]["title"] == "A Library"
            assert catalog["metadata"]["description"] == "New and improved"

            # The library's new data is also in the database.
            library = get_one(fixture.db.session, Library, opds_url=opds_url)
            assert library != None
            assert library.name == "A Library"
            assert library.description == "New and improved"
            assert library.web_url is None
            assert library.logo_url.endswith(LibraryLogoStore.logo_path(library, "png"))
            # The library's library_stage has been updated to reflect
            # the 'stage' method passed in from the client.
            assert library.library_stage == Library.TESTING_STAGE

            # There are still three Hyperlinks associated with the
            # library.
            help_link_2, copyright_agent_link_2, integration_contact_link_2 = sorted(
                library.hyperlinks, key=lambda x: x.rel
            )

            # The Hyperlink objects are the same as before.
            assert help_link == help_link_2
            assert copyright_agent_link == copyright_agent_link_2
            assert integration_contact_link == integration_contact_link_2

            # But two of the hrefs have been updated to reflect the new
            # authentication document.
            assert help_link.rel == "help"
            assert help_link.href == "mailto:new-help@library.org"
            assert copyright_agent_link.rel == Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL
            assert copyright_agent_link.href == "mailto:me@library.org"

            # The link that hasn't changed is unaffected.
            assert integration_contact_link.rel == Hyperlink.INTEGRATION_CONTACT_REL
            assert integration_contact_link.href == "mailto:me@library.org"

            # Two emails were sent out -- one asking for confirmation
            # of new-help@library.org, and one announcing the new role
            # for me@library.org (which already has an outstanding
            # confirmation request) as designated copyright agent.
            new_dmca, new_help = sorted(
                [(x[1], x[0]) for x in fixture.controller.emailer.sent_out]
            )
            assert new_dmca == ("me@library.org", Emailer.ADDRESS_DESIGNATED)
            assert new_help == (
                "new-help@library.org",
                Emailer.ADDRESS_NEEDS_CONFIRMATION,
            )

            # Commit to update library.service_areas.
            fixture.db.session.commit()

            # The library's service areas have been updated.
            [service_area] = library.service_areas
            assert service_area.place_id == fixture.db.connecticut_state.id

            # In addition to making the request to get the
            # Authentication For OPDS document, and the request to
            # get the root OPDS feed, the registry made a
            # follow-up request to download the library's logo.
            assert fixture.http_client.requests == [
                "http://circmanager.org/authentication.opds",
                "http://circmanager.org/feed/",
                "http://circmanager.org/logo.png",
            ]

        # If we include the old secret in a request and also set
        # reset_shared_secret, the registry will generate a new
        # secret.
        form_args_no_reset = ImmutableMultiDict(
            [
                ("url", "http://circmanager.org/authentication.opds"),
                ("contact", "mailto:me@library.org"),
            ]
        )
        form_args_with_reset = ImmutableMultiDict(
            list(form_args_no_reset.items()) + [("reset_shared_secret", "y")]
        )
        with fixture.app.test_request_context(
            "/", headers={"Authorization": "Bearer %s" % old_secret}, method="POST"
        ):
            flask.request.form = form_args_with_reset
            key = RSA.generate(1024)
            auth_document = self._auth_document(key)
            fixture.http_client.queue_response(
                200, content=json.dumps(auth_document), url=auth_document["id"]
            )
            self.queue_opds_success(fixture)

            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            assert response.status_code == 200
            catalog = json.loads(response.data)
            assert library.shared_secret != old_secret

            # The registry encrypted the new secret with the public key, and
            # it can be decrypted with the private key.
            encryptor = PKCS1_OAEP.new(key)
            encrypted_secret = base64.b64decode(catalog["metadata"]["shared_secret"])
            assert (
                encryptor.decrypt(encrypted_secret).decode("utf8")
                == library.shared_secret
            )

        old_secret = library.shared_secret

        # If we include an incorrect secret, or we don't ask for the
        # secret to be reset, the secret doesn't change.
        for secret, form in (
            ("notthesecret", form_args_with_reset),
            (library.shared_secret, form_args_no_reset),
        ):
            with fixture.app.test_request_context(
                "/", headers={"Authorization": "Bearer %s" % secret}
            ):
                flask.request.form = form

                key = RSA.generate(1024)
                auth_document = self._auth_document(key)
                fixture.http_client.queue_response(
                    200, content=json.dumps(auth_document)
                )
                self.queue_opds_success(fixture)

                response = fixture.controller.register(
                    do_get=fixture.http_client.do_get
                )

                assert response.status_code == 200
                assert library.shared_secret == old_secret

    def test_register_with_secret_changes_authentication_url_and_opds_url(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        fixture = registry_controller_fixture

        # This Library was created previously with a certain shared
        # secret, at a URL that's no longer valid.
        secret = "it's a secret"
        library = fixture.db.library()
        library.authentication_url = "http://old-url/authentication_document"
        library.opds_url = "http://old-url/opds"
        library.shared_secret = secret

        # We're going to register a library at an apparently new URL,
        # but since we're providing the shared secret for an existing
        # Library, the registry will know to modify that Library instead
        # of creating a new one.
        auth_document = self._auth_document()
        new_auth_url = auth_document["id"]
        [new_opds_url] = [
            x["href"] for x in auth_document["links"] if x["rel"] == "start"
        ]
        fixture.http_client.queue_response(
            200, content=json.dumps(auth_document), url=new_auth_url
        )
        self.queue_opds_success(fixture)
        with fixture.app.test_request_context("/", method="POST"):
            flask.request.headers = {"Authorization": "Bearer %s" % secret}
            flask.request.form = ImmutableMultiDict(
                [
                    ("url", new_auth_url),
                ]
            )
            response = fixture.controller.register(do_get=fixture.http_client.do_get)
            # No new library was created.
            assert response.status_code == 200

        # The library's authentication_url and opds_url have been modified.
        assert library.authentication_url == new_auth_url
        assert library.opds_url == new_opds_url

    def test_libraries_opds_crawlable(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        """Test basic crawlable feed functionality."""
        fixture = registry_controller_fixture
        base_time = utc_now()

        # Create 15 libraries with different timestamps.
        for i in range(15):
            lib = fixture.db.library(
                name=f"Library {i:02d}",
                short_name=f"lib{i}",
                library_stage=Library.PRODUCTION_STAGE,
                registry_stage=Library.PRODUCTION_STAGE,
            )
            # i=14 is newest (base_time + 14 days), so feed order is lib14, lib13, ...
            lib.timestamp = base_time + datetime.timedelta(days=i)
        fixture.db.session.flush()

        # Test first page.
        with fixture.app.test_request_context("/libraries/crawlable"):
            response = fixture.controller.libraries_opds_crawlable()

            assert response.status == "200 OK"
            assert response.headers["Content-Type"] == OPDSCatalog.OPDS_TYPE

            catalog = json.loads(response.data)
            # Should include all libraries (less than default page size of 100).
            # Note: fixture already has 3 default libraries from setup.
            assert len(catalog["catalogs"]) == 18

            # Check ordering: newest first (Library 14).
            first_lib = catalog["catalogs"][0]
            assert "Library 14" in first_lib["metadata"]["title"]

            # Check metadata includes total count.
            assert catalog["metadata"]["numberOfItems"] == 18

            # Check pagination links.
            links = {link["rel"]: link for link in catalog["links"]}
            assert "first" in links
            assert "self" in links
            assert "last" in links
            assert "next" not in links  # No next page (all results fit in one page).
            assert "previous" not in links  # First page has no previous.

    def test_libraries_opds_crawlable_pagination(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        """Test pagination across multiple pages."""
        fixture = registry_controller_fixture
        base_time = utc_now()

        size = Pagination.MIN_SIZE

        # Create enough libraries for three pages so that
        # the middle page has all four nav links.
        n = size * 2 + 3  # Three pages: full, full, partial.
        for i in range(n):
            lib = fixture.db.library(
                # Z prefix to sort after default fixtures
                name=f"ZLib {i:03d}",
                short_name=f"zlib{i}",
                library_stage=Library.PRODUCTION_STAGE,
                registry_stage=Library.PRODUCTION_STAGE,
            )
            lib.timestamp = base_time - datetime.timedelta(seconds=i)
        fixture.db.session.flush()

        total_expected = n + 3  # n new + 3 from fixture.

        # Test second page — should have prev, next, first, and last links.
        with fixture.app.test_request_context(
            f"/libraries/crawlable?offset={size}&size={size}"
        ):
            response = fixture.controller.libraries_opds_crawlable()
            catalog = json.loads(response.data)

            assert len(catalog["catalogs"]) == size

            total = catalog["metadata"]["numberOfItems"]
            assert total == total_expected

            links = {link["rel"]: link for link in catalog["links"]}
            assert "first" in links
            assert "previous" in links
            assert "next" in links
            assert "last" in links

            assert f"offset=0&size={size}" in links["first"]["href"]
            assert f"offset=0&size={size}" in links["previous"]["href"]
            assert f"offset={size * 2}&size={size}" in links["next"]["href"]

            expected_last = ((total_expected - 1) // size) * size
            assert f"offset={expected_last}&size={size}" in links["last"]["href"]

    def test_libraries_opds_crawlable_ordering(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        """Test deterministic ordering: timestamp DESC, name ASC (case-insensitive)."""
        fixture = registry_controller_fixture
        now = utc_now()

        # Create libraries with same timestamp but different names.
        for name in ["zebra", "Apple", "Banana", "aardvark"]:
            lib = fixture.db.library(
                name=name,
                short_name=name.lower()[:16],  # Short name has max length.
                library_stage=Library.PRODUCTION_STAGE,
                registry_stage=Library.PRODUCTION_STAGE,
            )
            lib.timestamp = now
        fixture.db.session.flush()

        with fixture.app.test_request_context("/libraries/crawlable"):
            response = fixture.controller.libraries_opds_crawlable()
            catalog = json.loads(response.data)

            # Find our test libraries (all have same recent timestamp, should appear first).
            test_libs = catalog["catalogs"][:4]

            # Should be in case-insensitive alphabetical order.
            names = [lib["metadata"]["title"] for lib in test_libs]
            assert names == ["aardvark", "Apple", "Banana", "zebra"]

    def test_libraries_opds_crawlable_qa(
        self, registry_controller_fixture: LibraryRegistryControllerFixture
    ):
        """Test QA crawlable feed includes testing libraries."""
        fixture = registry_controller_fixture

        # Create testing library.
        test_lib = fixture.db.library(
            name="Testing Library",
            short_name="test_lib",
            library_stage=Library.TESTING_STAGE,
            registry_stage=Library.TESTING_STAGE,
        )
        fixture.db.session.flush()

        # Production feed should not include testing library.
        with fixture.app.test_request_context("/libraries/crawlable"):
            response = fixture.controller.libraries_opds_crawlable(live=True)
            catalog = json.loads(response.data)
            titles = [cat["metadata"]["title"] for cat in catalog["catalogs"]]
            assert "Testing Library" not in titles
            prod_count = catalog["metadata"]["numberOfItems"]

        # QA feed should include testing library.
        with fixture.app.test_request_context("/libraries/qa/crawlable"):
            response = fixture.controller.libraries_opds_crawlable(live=False)
            catalog = json.loads(response.data)
            titles = [cat["metadata"]["title"] for cat in catalog["catalogs"]]
            assert "Testing Library" in titles

            # QA feed should have more libraries than production.
            qa_count = catalog["metadata"]["numberOfItems"]
            assert qa_count == prod_count + 1


class TestValidationController:
    def test_html_response(self, controller_setup_fixture: ControllerSetupFixture):
        with controller_setup_fixture.setup() as fixture:
            # Test the generation of a simple HTML-based HTTP response.
            controller = ValidationController(fixture.library_registry)
            response = controller.html_response(999, "a message")
            assert response.status_code == 999
            assert response.headers["Content-Type"] == "text/html"
            assert response.data.decode("utf8") == controller.MESSAGE_TEMPLATE % dict(
                message="a message"
            )

    def test_validate(self, controller_setup_fixture: ControllerSetupFixture):
        with controller_setup_fixture.setup() as fixture:

            class Mock(ValidationController):
                def html_response(self, status_code, message):
                    return (status_code, message)

            controller = Mock(fixture.library_registry)

            def assert_response(resource_id, secret, status_code, message):
                """Invoke the validate() method with the given secret
                and verify that html_response is called with the given
                status_code and message.
                """
                result = controller.confirm(resource_id, secret)
                assert result == (status_code, message)

            # This library has three links: two that are in the middle of
            # the validation process and one that has not started the
            # validation process.
            library = fixture.db.library()

            link1, ignore = library.set_hyperlink("rel", "mailto:1@library.org")
            needs_validation = link1.resource
            needs_validation.restart_validation()
            secret = needs_validation.validation.secret

            link2, ignore = library.set_hyperlink("rel2", "mailto:2@library.org")
            needs_validation_2 = link2.resource
            needs_validation_2.restart_validation()
            secret2 = needs_validation_2.validation.secret

            link3, ignore = library.set_hyperlink("rel2", "mailto:3@library.org")
            not_started = link3.resource  # noqa: F841

            # Simple tests for missing fields or failed lookups.
            assert_response(
                needs_validation.id, "", 404, "No confirmation code provided"
            )
            assert_response(None, "a code", 404, "No resource ID provided")
            assert_response(-20, secret, 404, "No such resource")

            # Secret does not exist.
            assert_response(
                needs_validation.id,
                "nosuchcode",
                404,
                "Confirmation code 'nosuchcode' not found",
            )

            # Secret exists but is associated with a different Resource.
            assert_response(
                needs_validation.id,
                secret2,
                404,
                "Confirmation code %r not found" % secret2,
            )

            # Secret exists but is not associated with any Resource (this
            # shouldn't happen).
            needs_validation_2.validation.resource = None
            assert_response(
                needs_validation.id,
                secret2,
                404,
                "Confirmation code %r not found" % secret2,
            )

            # Secret matches resource but validation has expired.
            needs_validation.validation.started_at = utc_now() - datetime.timedelta(
                days=7
            )
            assert_response(
                needs_validation.id,
                secret,
                400,
                "Confirmation code %r has expired. Re-register to get another code."
                % secret,
            )

            # Success.
            needs_validation.restart_validation()
            secret = needs_validation.validation.secret
            assert_response(
                needs_validation.id,
                secret,
                200,
                "You successfully confirmed mailto:1@library.org.",
            )

            # A Resource can't be validated twice.
            assert_response(
                needs_validation.id, secret, 200, "This URI has already been validated."
            )


class TestCoverageController:
    def parse_to(
        self,
        fixture: ControllerFixture,
        coverage,
        places=[],
        ambiguous=None,
        unknown=None,
        to_json=True,
    ):
        # Make a request to the coverage controller to turn a coverage
        # object into GeoJSON. Verify that the Places in
        # `places` are represented in the coverage object
        # and that the 'ambiguous' and 'unknown' extensions
        # are also as expected.
        if to_json:
            coverage = json.dumps(coverage)
        with fixture.app.test_request_context(
            "/?coverage=%s" % coverage, method="POST"
        ):
            response = self.controller.lookup()

        # The response is always GeoJSON.
        assert response.headers["Content-Type"] == "application/geo+json"
        geojson = json.loads(response.data)

        # Unknown or ambiguous places will be mentioned in
        # these extra fields.
        actual_unknown = geojson.pop("unknown", None)
        assert unknown == actual_unknown
        actual_ambiguous = geojson.pop("ambiguous", None)
        assert actual_ambiguous == ambiguous

        # Without those extra fields, the GeoJSON document should be
        # identical to the one we get by calling Place.to_geojson
        # on the expected places.
        expect_geojson = Place.to_geojson(fixture.db.session, *places)
        assert expect_geojson == geojson

    def test_lookup(self, controller_setup_fixture: ControllerSetupFixture):
        with controller_setup_fixture.setup() as fixture:
            self.controller = CoverageController(fixture.library_registry)

            # Set up a default nation to make it easier to test a variety
            # of coverage area types.
            ConfigurationSetting.sitewide(
                fixture.db.session, Configuration.DEFAULT_NATION_ABBREVIATION
            ).value = "US"

            # Set up some places.
            kansas = fixture.db.kansas_state
            massachussets = fixture.db.massachussets_state
            boston = fixture.db.boston_ma

            # Parse some strings to GeoJSON objects.
            self.parse_to(fixture, "Boston, MA", [boston], to_json=False)
            self.parse_to(fixture, "Boston, MA", [boston], to_json=True)
            self.parse_to(fixture, "Massachussets", [massachussets])
            self.parse_to(fixture, ["Massachussets", "Kansas"], [massachussets, kansas])
            self.parse_to(fixture, {"US": "Kansas"}, [kansas])
            self.parse_to(
                fixture, {"US": ["Massachussets", "Kansas"]}, [massachussets, kansas]
            )
            self.parse_to(fixture, ["KS", "UT"], [kansas], unknown={"US": ["UT"]})

            # Creating two states with the same name is the simplest way
            # to create an ambiguity problem.
            massachussets.external_name = "Kansas"
            self.parse_to(fixture, "Kansas", [], ambiguous={"US": ["Kansas"]})

    def test_library_eligibility_and_focus(
        self, controller_setup_fixture: ControllerSetupFixture
    ):
        with controller_setup_fixture.setup() as fixture:
            self.controller = CoverageController(fixture.library_registry)

            # focus_for_library() and eligibility_for_library() represent
            # a library's service area as GeoJSON.

            # We don't use self.nypl here because we want to set more
            # realistic service and focus areas.
            nypl = fixture.db.library("NYPL")

            # New York State is the eligibility area for NYPL.
            get_one_or_create(
                fixture.db.session,
                ServiceArea,
                library=nypl,
                place=fixture.db.new_york_state,
                type=ServiceArea.ELIGIBILITY,
            )

            # New York City is the focus area.
            get_one_or_create(
                fixture.db.session,
                ServiceArea,
                library=nypl,
                place=fixture.db.new_york_city,
                type=ServiceArea.FOCUS,
            )

            with self.request_context_with_library("/", library=nypl):
                focus = (
                    fixture.app.library_registry.coverage_controller.focus_for_library()
                )
                eligibility = (
                    fixture.app.library_registry.coverage_controller.eligibility_for_library()
                )

                # In both cases we got a GeoJSON document
                for response in (focus, eligibility):
                    assert response.status_code == 200
                    assert response.headers["Content-Type"] == "application/geo+json"

                # The GeoJSON documents are the ones we'd expect from turning
                # the corresponding service areas into GeoJSON.
                focus = json.loads(focus.data)
                assert focus == Place.to_geojson(
                    fixture.db.session, fixture.db.new_york_city
                )

                eligibility = json.loads(eligibility.data)
                assert eligibility == Place.to_geojson(
                    fixture.db.session, fixture.db.new_york_state
                )
