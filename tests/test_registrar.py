import json

from authentication_document import AuthenticationDocument
from opds import OPDSCatalog
from problem_details import *
from registrar import LibraryRegistrar
from testing import DatabaseTest, DummyHTTPResponse
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

    def test__required_email_address(self):
        """Validate the code that makes sure an input is a mailto: URI."""
        uri = INVALID_CONTACT_URI.uri
        m = LibraryRegistrar._required_email_address

        problem = m(None, "a title")
        assert problem.uri == uri
        # The custom title is used.
        assert problem.title == "a title"
        assert problem.detail == "No email address was provided"

        # Changing the title doesn't affect the original ProblemDetail
        # document.
        assert "a title" != INVALID_CONTACT_URI.title

        problem = m("http://not-an-email/", "a title")
        assert problem.uri == uri
        assert (
            problem.detail
            == "URI must start with 'mailto:' (got: http://not-an-email/)"
        )

        mailto = "mailto:me@library.org"
        success = m(mailto, "a title")
        assert success == mailto

    def test__locate_email_addresses(self):
        """Test the code that finds an email address in a list of links."""
        uri = INVALID_CONTACT_URI.uri
        m = LibraryRegistrar._locate_email_addresses

        # No links at all.
        result = m("rel0", [], "a title")
        assert isinstance(result, ProblemDetail)
        assert result.uri == uri
        assert result.title == "a title"
        assert result.detail == "No valid mailto: links found with rel=rel0"

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
        assert result.detail == "No valid mailto: links found with rel=rel1"

        # Multiple links that work.
        result = m("rel2", links, "a title")
        assert result == ["mailto:me@library.org", "mailto:me2@library.org"]
