import base64
import json
from io import BytesIO
from unittest.mock import MagicMock, patch

from authentication_document import AuthenticationDocument
from model import Library
from opds import OPDSCatalog
from problem_details import INVALID_CONTACT_URI, NO_AUTH_URL
from registrar import LibraryRegistrar, VerifyLinkRegexes
from testing import DatabaseTest, DummyHTTPResponse
from tests.utils import mock_response
from util.problem_detail import ProblemDetail


class TestRegistrar(DatabaseTest):

    # TODO: The core method, register(), is tested indirectly in
    # test_controller.py, because the LibraryRegistrar code was
    # originally part of LibraryRegistryController. This could be
    # refactored.

    def test_reregister(self):
        class Mock(LibraryRegistrar):
            RETURN_VALUE = NO_AUTH_URL

            def register(self, library, library_stage):
                self.called_with = (library, library_stage)
                return self.RETURN_VALUE

        library = self._library()
        registrar = Mock(object(), object())

        # Test the case where register() returns a problem detail.
        result = registrar.reregister(library)
        assert registrar.called_with == (library, library.library_stage)
        assert result == Mock.RETURN_VALUE

        # If register() returns anything other than a problem detail,
        # we presume success and return nothing.
        registrar.RETURN_VALUE = (object(), object(), object())
        result = registrar.reregister(library)
        assert result is None

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
        has_link_feed = '<feed><link rel="%s" href="%s"/></feed>' % (rel, auth_url)
        response = DummyHTTPResponse(
            200, {"Content-Type": OPDSCatalog.OPDS_1_TYPE}, has_link_feed
        )
        assert LibraryRegistrar.opds_response_links(response, rel) == [auth_url]
        assert (
            LibraryRegistrar.opds_response_links_to_auth_document(response, auth_url)
            is True
        )
        assert (
            LibraryRegistrar.opds_response_links_to_auth_document(
                response, "Some other URL"
            )
            is False
        )

        # The same feed, but with an additional link in the
        # Link header. Both links are returned.
        response = DummyHTTPResponse(
            200,
            {"Content-Type": OPDSCatalog.OPDS_1_TYPE},
            has_link_feed,
            links={rel: {"url": "http://another-auth-document", "rel": rel}},
        )
        assert set(LibraryRegistrar.opds_response_links(response, rel)) == set(
            [auth_url, "http://another-auth-document"]
        )
        assert (
            LibraryRegistrar.opds_response_links_to_auth_document(response, auth_url)
            is True
        )

        # A similar feed, but with a relative URL, which is made absolute
        # by opds_response_links.
        relative_url_feed = '<feed><link rel="%s" href="auth-document"/></feed>' % (rel)
        response = DummyHTTPResponse(
            200, {"Content-Type": OPDSCatalog.OPDS_1_TYPE}, relative_url_feed
        )
        response.url = "http://opds-server/catalog.opds"
        assert LibraryRegistrar.opds_response_links(response, rel) == [
            "http://opds-server/auth-document"
        ]
        assert (
            LibraryRegistrar.opds_response_links_to_auth_document(
                response, "http://opds-server/auth-document"
            )
            is True
        )

        # An OPDS 1 feed that has no link.
        response = DummyHTTPResponse(
            200, {"Content-Type": OPDSCatalog.OPDS_1_TYPE}, "<feed></feed>"
        )
        assert LibraryRegistrar.opds_response_links(response, rel) == []
        assert (
            LibraryRegistrar.opds_response_links_to_auth_document(response, auth_url)
            is False
        )

        # An OPDS 2 feed that has a link.
        catalog = json.dumps({"links": {rel: {"href": auth_url}}})
        response = DummyHTTPResponse(
            200, {"Content-Type": OPDSCatalog.OPDS_TYPE}, catalog
        )
        assert LibraryRegistrar.opds_response_links(response, rel) == [auth_url]
        assert (
            LibraryRegistrar.opds_response_links_to_auth_document(response, auth_url)
            is True
        )

        # An OPDS 2 feed that has no link.
        catalog = json.dumps({"links": {}})
        response = DummyHTTPResponse(
            200, {"Content-Type": OPDSCatalog.OPDS_TYPE}, catalog
        )
        assert LibraryRegistrar.opds_response_links(response, rel) == []
        assert (
            LibraryRegistrar.opds_response_links_to_auth_document(response, auth_url)
            is False
        )

        # A malformed feed.
        response = DummyHTTPResponse(
            200, {"Content-Type": OPDSCatalog.OPDS_TYPE}, "Not a real feed"
        )
        assert (
            LibraryRegistrar.opds_response_links_to_auth_document(response, auth_url)
            is False
        )

        # An Authentication For OPDS document.
        response = DummyHTTPResponse(
            200,
            {"Content-Type": AuthenticationDocument.MEDIA_TYPE},
            json.dumps({"id": auth_url}),
        )
        assert LibraryRegistrar.opds_response_links(response, rel) == [auth_url]
        assert (
            LibraryRegistrar.opds_response_links_to_auth_document(response, auth_url)
            is True
        )

        # A malformed Authentication For OPDS document.
        response = DummyHTTPResponse(
            200,
            {"Content-Type": AuthenticationDocument.MEDIA_TYPE},
            json.dumps("Not a document."),
        )
        assert LibraryRegistrar.opds_response_links(response, rel) == []
        assert (
            LibraryRegistrar.opds_response_links_to_auth_document(response, auth_url)
            is False
        )

    def test__required_link_type(self):
        """Validate the code that makes sure an input is a mailto: URI."""
        uri = INVALID_CONTACT_URI.uri
        m = LibraryRegistrar._required_link_type

        problem = m(None, "a title", VerifyLinkRegexes.MAILTO)
        assert problem.uri == uri
        # The custom title is used.
        assert problem.title == "a title"
        assert problem.detail == "No link href was provided"

        # Changing the title doesn't affect the original ProblemDetail
        # document.
        assert "a title" != INVALID_CONTACT_URI.title

        problem = m("http://not-an-email/", "a title", VerifyLinkRegexes.MAILTO)
        assert problem.uri == uri
        assert problem.detail == "URI must match '^mailto:' (got: http://not-an-email/)"

        mailto = "mailto:me@library.org"
        success = m(mailto, "a title", VerifyLinkRegexes.MAILTO)
        assert success == mailto

        uri = "https://library.org"
        success = m(uri, "a title", VerifyLinkRegexes.HTTP_OR_MAILTO)
        assert success == uri

        uri = "mailto:help@library.org"
        success = m(uri, "a title", VerifyLinkRegexes.HTTP_OR_MAILTO)
        assert success == uri

    def test__verify_links(self):
        """Test the code that finds an email address in a list of links."""
        uri = INVALID_CONTACT_URI.uri
        m = LibraryRegistrar._verify_links

        # No links at all.
        result = m("rel0", [], "a title")
        assert isinstance(result, ProblemDetail)
        assert result.uri == uri
        assert result.title == "a title"
        assert result.detail == "No valid '^mailto:' links found with rel=rel0"

        # Links exist but none are valid and relevant.
        links = [
            dict(rel="rel1", href="http://foo/"),
            dict(rel="rel1", href="http://bar/"),
            dict(rel="rel2", href="mailto:me@library.org"),
            dict(rel="rel2", href="mailto:me2@library.org"),
        ]
        result = m("rel1", links, "a title")
        assert isinstance(result, ProblemDetail)
        assert result.uri == uri
        assert result.title == "a title"
        assert result.detail == "No valid '^mailto:' links found with rel=rel1"

        # Multiple links that work.
        result = m("rel2", links, "a title")
        assert result == ["mailto:me@library.org", "mailto:me2@library.org"]

    def _auth_document(self):
        return {
            "id": "http://auth",
            "title": "Test",
            "authentication": [],
            "links": [
                {
                    "rel": "start",
                    "type": "application/atom+xml;profile=opds-catalog;kind=acquisition",
                    "href": "http://auth",
                },
                {"rel": "help", "href": "mailto:help@example.org", "type": None},
                {
                    "rel": "http://opds-spec.org/shelf",
                    "href": "http://localhost:6500/localtest/loans/",
                    "type": "application/atom+xml;profile=opds-catalog;kind=acquisition",
                },
                {
                    "rel": "http://librarysimplified.org/terms/rel/user-profile",
                    "href": "http://localhost:6500/localtest/patrons/me/",
                    "type": "vnd.librarysimplified/user-profile+json",
                },
                {
                    "rel": "http://librarysimplified.org/rel/designated-agent/copyright",
                    "href": "mailto:help@example.org",
                },
            ],
        }

    @patch("registrar.LibraryLogoStore")
    def test_register_logo_data(self, mock_logo_store):
        """Test an auth document with base64 encoded image data"""
        image_data = "data:image/png;base64,abcdefg"
        auth_document = self._auth_document()
        auth_document["links"].append(
            {
                "rel": "logo",
                "type": "image/png",
                "href": image_data,
            }
        )

        library: Library = self._library(registry_stage=Library.TESTING_STAGE)
        library.authentication_url = "http://auth"
        registrar = LibraryRegistrar(self._db)

        registrar._make_request = MagicMock(
            return_value=mock_response(
                200,
                auth_document,
                url="http://auth",
                headers={"Content-Type": OPDSCatalog.OPDS_1_TYPE},
            )
        )
        mock_logo_store.write_raw.return_value = "http://localhost/logo"
        with patch(
            "registrar.LibraryRegistrar.opds_response_links_to_auth_document"
        ) as mock_fn:
            mock_fn.return_value = True
            registrar.register(library, Library.TESTING_STAGE)

        assert registrar._make_request.call_count == 2
        assert mock_logo_store.write_raw.call_count == 1

        args = mock_logo_store.write_raw.call_args
        assert args[0][0] == library
        assert args[0][1] == image_data

        assert library.logo_url == "http://localhost/logo"

    @patch("registrar.LibraryLogoStore")
    def test_register_logo_links(self, mock_logo_store):
        """Test an auth document with an image link"""
        image_link = "http://somelogolink"
        auth_document = self._auth_document()
        auth_document["links"].append(
            {
                "rel": "logo",
                "type": "image/png",
                "href": image_link,
            }
        )

        library: Library = self._library(registry_stage=Library.TESTING_STAGE)
        library.authentication_url = "http://auth"
        registrar = LibraryRegistrar(self._db)

        registrar._make_request = MagicMock(
            return_value=mock_response(
                200,
                auth_document,
                url="http://auth",
                headers={"Content-Type": OPDSCatalog.OPDS_1_TYPE},
            )
        )
        small_png = base64.b64decode(
            b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        registrar.do_get = MagicMock(
            return_value=mock_response(200, small_png, stream=True)
        )
        mock_logo_store.write.return_value = "http://localhost/logo"

        with patch(
            "registrar.LibraryRegistrar.opds_response_links_to_auth_document"
        ) as mock_fn:
            mock_fn.return_value = True
            registrar.register(library, Library.TESTING_STAGE)

        assert registrar._make_request.call_count == 2
        assert mock_logo_store.write.call_count == 1

        args = mock_logo_store.write.call_args
        assert args[0][0] == library
        assert type(args[0][1]) == BytesIO

        assert library.logo_url == "http://localhost/logo"
