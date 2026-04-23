import pytest
from flask import url_for

from palace.registry.controller import (
    OPDS_CATALOG_REGISTRATION_MEDIA_TYPE,
    OPENSEARCH_MEDIA_TYPE,
)
from palace.registry.opds import OPDSCatalog
from palace.registry.route_links import RouteLinkRegistry


class TestRouteLinkRegistry:
    @staticmethod
    def _make_url_for():
        """Return a url_for that builds http://example.com/<endpoint>."""
        return lambda endpoint, **kwargs: f"http://example.com/{endpoint}"

    def test_links_result_dict(self):
        registry = RouteLinkRegistry()

        @registry.register(rel="search", type="application/json")
        def my_route():
            pass

        [link] = list(registry.links(self._make_url_for()))
        assert link == {
            "href": "http://example.com/my_route",
            "rel": "search",
            "type": "application/json",
        }

    def test_links_url_kwargs_passed_to_url_for(self):
        registry = RouteLinkRegistry()

        @registry.register(rel="library", url_kwargs={"uuid": "test-uuid"})
        def my_route():
            pass

        calls = []

        def url_for(endpoint, **kwargs):
            calls.append((endpoint, kwargs))
            return "http://example.com"

        list(registry.links(url_for))
        assert calls == [("my_route", {"uuid": "test-uuid"})]

    def test_links_registration_order_preserved(self):
        registry = RouteLinkRegistry()

        @registry.register(rel="first")
        def route_a():
            pass

        @registry.register(rel="second")
        def route_b():
            pass

        links = list(registry.links(self._make_url_for()))
        assert [link["rel"] for link in links] == ["first", "second"]

    @pytest.mark.parametrize(
        "registered,links_production_only,included",
        [
            pytest.param(None, True, True, id="registered-always-requested-prod-only"),
            pytest.param(
                None, False, True, id="registered-always-requested-with-hidden"
            ),
            pytest.param(
                True, True, True, id="registered-prod-only-requested-prod-only"
            ),
            pytest.param(
                True, False, False, id="registered-prod-only-requested-with-hidden"
            ),
            pytest.param(
                False, True, False, id="registered-with-hidden-requested-prod-only"
            ),
            pytest.param(
                False, False, True, id="registered-with-hidden-requested-with-hidden"
            ),
        ],
    )
    def test_links_production_only_filter(
        self, registered, links_production_only, included
    ):
        registry = RouteLinkRegistry()

        @registry.register(rel="test", production_only=registered)
        def my_route():
            pass

        links = list(
            registry.links(self._make_url_for(), production_only=links_production_only)
        )
        assert (len(links) > 0) == included

    @pytest.mark.parametrize(
        "encoded,expected",
        [
            pytest.param("%7Buuid%7D%2Fpath", "{uuid}%2Fpath", id="uppercase"),
            pytest.param("%7buuid%7d%2Fpath", "{uuid}%2Fpath", id="lowercase"),
            pytest.param("%7Buuid%7d%2Fpath", "{uuid}%2Fpath", id="mixed"),
        ],
    )
    def test_links_templated_unquotes_braces(self, encoded, expected):
        """Only `{` and `}` are unquoted; other percent-encoded characters are preserved."""
        registry = RouteLinkRegistry()

        @registry.register(rel="library", templated=True, url_kwargs={"uuid": "{uuid}"})
        def my_route():
            pass

        [link] = list(
            registry.links(lambda endpoint, **kw: f"http://example.com/{encoded}")
        )
        assert link["href"] == f"http://example.com/{expected}"

    def test_links_non_templated_preserves_encoding(self):
        registry = RouteLinkRegistry()

        @registry.register(rel="test")
        def my_route():
            pass

        [link] = list(
            registry.links(lambda endpoint, **kw: "http://example.com/%7Bfoo%7D")
        )
        assert link["href"] == "http://example.com/%7Bfoo%7D"


class TestRouteLinkRegistryApp:
    """Verifies that endpoint names registered in the app's route_links resolve correctly."""

    @pytest.mark.parametrize(
        "production_only,expected",
        [
            pytest.param(
                True,
                [
                    {
                        "href": "http://localhost/register",
                        "rel": "register",
                        "type": OPDS_CATALOG_REGISTRATION_MEDIA_TYPE,
                    },
                    {
                        "href": "http://localhost/search",
                        "rel": "search",
                        "type": OPENSEARCH_MEDIA_TYPE,
                    },
                    {
                        "href": "http://localhost/libraries",
                        "rel": "current",
                        "type": OPDSCatalog.OPDS_TYPE,
                    },
                    {
                        "href": "http://localhost/libraries/crawlable",
                        "rel": "paged",
                        "type": OPDSCatalog.OPDS_TYPE,
                    },
                    {
                        "href": "http://localhost/library/{uuid}",
                        "rel": "http://librarysimplified.org/rel/registry/library",
                        "type": OPDSCatalog.OPDS_TYPE,
                        "templated": True,
                    },
                ],
                id="production-only",
            ),
            pytest.param(
                False,
                [
                    {
                        "href": "http://localhost/register",
                        "rel": "register",
                        "type": OPDS_CATALOG_REGISTRATION_MEDIA_TYPE,
                    },
                    {
                        "href": "http://localhost/qa/search",
                        "rel": "search",
                        "type": OPENSEARCH_MEDIA_TYPE,
                    },
                    {
                        "href": "http://localhost/libraries/qa",
                        "rel": "current",
                        "type": OPDSCatalog.OPDS_TYPE,
                    },
                    {
                        "href": "http://localhost/libraries/crawlable",
                        "rel": "paged",
                        "type": OPDSCatalog.OPDS_TYPE,
                    },
                    {
                        "href": "http://localhost/library/{uuid}",
                        "rel": "http://librarysimplified.org/rel/registry/library",
                        "type": OPDSCatalog.OPDS_TYPE,
                        "templated": True,
                    },
                ],
                id="with-hidden",
            ),
        ],
    )
    def test_links_resolve(self, application, production_only, expected):
        from app import app, route_links

        with app.test_request_context("/"):
            links = list(
                route_links.links(
                    lambda endpoint, **kw: url_for(endpoint, _external=True, **kw),
                    production_only=production_only,
                )
            )

        assert links == expected
