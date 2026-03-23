from __future__ import annotations

import datetime
import json

import pytest

from palace.registry.authentication_document import AuthenticationDocument
from palace.registry.config import Configuration
from palace.registry.opds import AvailabilityFacet, OPDSCatalog, OrderFacet
from palace.registry.sqlalchemy.constants import LibraryType
from palace.registry.sqlalchemy.model.configuration_setting import ConfigurationSetting
from palace.registry.sqlalchemy.model.hyperlink import Hyperlink
from palace.registry.sqlalchemy.model.library import Library
from palace.registry.sqlalchemy.model.resource import Validation
from palace.registry.sqlalchemy.util import create
from palace.registry.util.datetime_helpers import utc_now
from tests.fixtures.database import DatabaseTransactionFixture


class TestOrderFacet:
    """Tests for OrderFacet enum."""

    @pytest.mark.parametrize(
        "facet, expected_count",
        [
            pytest.param(OrderFacet.MODIFIED, 3, id="modified"),
            pytest.param(OrderFacet.MODIFIED_ASC, 3, id="modified-asc"),
            pytest.param(OrderFacet.NAME, 3, id="name"),
            pytest.param(OrderFacet.NAME_DESC, 3, id="name-desc"),
            pytest.param(OrderFacet.NATURAL, 0, id="natural"),
            pytest.param(OrderFacet.DEFAULT, 3, id="default"),
        ],
    )
    def test_sort_order_expression_count(self, facet, expected_count):
        assert len(facet.sort_order_expressions) == expected_count

    def test_default_is_alias_for_modified(self):
        """DEFAULT produces the same SQL expressions as MODIFIED."""
        assert [str(e) for e in OrderFacet.DEFAULT.sort_order_expressions] == [
            str(e) for e in OrderFacet.MODIFIED.sort_order_expressions
        ]

    @pytest.mark.parametrize(
        "facet, expected_group",
        [
            pytest.param(OrderFacet.MODIFIED, "modified", id="modified"),
            pytest.param(OrderFacet.MODIFIED_ASC, "modified", id="modified-asc"),
            pytest.param(OrderFacet.NAME, "name", id="name"),
            pytest.param(OrderFacet.NAME_DESC, "name", id="name-desc"),
            pytest.param(OrderFacet.NATURAL, None, id="natural-singleton"),
            pytest.param(OrderFacet.DEFAULT, "modified", id="default-alias"),
        ],
    )
    def test_group(self, facet, expected_group):
        assert facet.group == expected_group

    def test_advertised_facets(self):
        assert OrderFacet.advertised_facets() == [
            OrderFacet.MODIFIED,
            OrderFacet.MODIFIED_ASC,
            OrderFacet.NAME,
            OrderFacet.NAME_DESC,
            OrderFacet.NATURAL,
        ]


class TestAvailabilityFacet:
    def test_labels(self):
        assert AvailabilityFacet.PRODUCTION.label == "Production"
        assert AvailabilityFacet.HIDDEN.label == "Hidden"
        assert AvailabilityFacet.ALL.label == "All: Production and Hidden"

    def test_advertised_facets(self):
        facets = AvailabilityFacet.advertised_facets()
        assert facets == [
            AvailabilityFacet.PRODUCTION,
            AvailabilityFacet.HIDDEN,
            AvailabilityFacet.ALL,
        ]

    def test_str_values(self):
        assert AvailabilityFacet.PRODUCTION == "production"
        assert AvailabilityFacet.HIDDEN == "hidden"
        assert AvailabilityFacet.ALL == "all"


class TestAddFacets:
    """Tests for OPDSCatalog._add_facets."""

    BASE_URL = "https://registry.example.org/libraries/crawlable"

    def _make_catalog(self, order_str=None, availability_str=None):
        """Build a minimal catalog dict and run _add_facets on it."""
        catalog = OPDSCatalog.__new__(OPDSCatalog)
        catalog.catalog = {"metadata": {}, "links": [], "catalogs": []}
        catalog._add_facets(self.BASE_URL, order_str, availability_str)
        return catalog.catalog

    def test_facets_structure(self):
        """Facets array has sort and availability groups with expected metadata."""
        cat = self._make_catalog()
        facets = cat["facets"]
        assert len(facets) == 2
        assert facets[0]["metadata"]["title"] == "Sort by"
        assert facets[0]["metadata"]["@type"] == OPDSCatalog.SORT_FACET_TYPE
        assert facets[1]["metadata"]["title"] == "Availability"
        assert facets[1]["metadata"]["@type"] == OPDSCatalog.AVAILABILITY_FACET_TYPE

    def test_sort_facet_counts(self):
        cat = self._make_catalog()
        # advertised_facets returns [MODIFIED, MODIFIED_ASC, NAME, NAME_DESC, NATURAL]
        assert len(cat["facets"][0]["links"]) == 5

    def test_availability_facet_counts(self):
        cat = self._make_catalog()
        # Three values: production, hidden, all
        assert len(cat["facets"][1]["links"]) == 3

    @pytest.mark.parametrize(
        "order_str, availability_str, expected_active_order, expected_active_avail_label",
        [
            pytest.param(None, None, "modified", "Production", id="defaults"),
            pytest.param(
                "modified",
                "production",
                "modified",
                "Production",
                id="explicit-defaults",
            ),
            pytest.param("name", "hidden", "name", "Hidden", id="non-defaults"),
            pytest.param(
                "name", "all", "name", "All: Production and Hidden", id="all-avail"
            ),
        ],
    )
    def test_active_facet_has_rel_self(
        self,
        order_str,
        availability_str,
        expected_active_order,
        expected_active_avail_label,
    ):
        cat = self._make_catalog(order_str, availability_str)
        sort_links = cat["facets"][0]["links"]
        avail_links = cat["facets"][1]["links"]

        active_sort = [l for l in sort_links if l.get("rel") == "self"]
        assert len(active_sort) == 1
        assert active_sort[0]["title"] == OrderFacet(expected_active_order).label

        active_avail = [l for l in avail_links if l.get("rel") == "self"]
        assert len(active_avail) == 1
        assert active_avail[0]["title"] == expected_active_avail_label

    def test_default_facets_have_default_property(self):
        """MODIFIED and PRODUCTION facets carry the default property regardless of active."""
        cat = self._make_catalog(order_str="name", availability_str="hidden")
        sort_links = cat["facets"][0]["links"]
        avail_links = cat["facets"][1]["links"]

        default_sort = [
            l
            for l in sort_links
            if l.get("properties", {}).get(OPDSCatalog.PALACE_PROPERTIES_DEFAULT)
        ]
        assert len(default_sort) == 1
        assert default_sort[0]["title"] == OrderFacet.MODIFIED.label

        default_avail = [
            l
            for l in avail_links
            if l.get("properties", {}).get(OPDSCatalog.PALACE_PROPERTIES_DEFAULT)
        ]
        assert len(default_avail) == 1
        assert default_avail[0]["title"] == "Production"

    def test_active_and_default_simultaneously(self):
        """When no params given, modified and production are both active and default."""
        cat = self._make_catalog()
        sort_links = cat["facets"][0]["links"]
        avail_links = cat["facets"][1]["links"]

        modified_link = next(l for l in sort_links if "modified" in l["href"])
        assert modified_link.get("rel") == "self"
        assert (
            modified_link.get("properties", {}).get(
                OPDSCatalog.PALACE_PROPERTIES_DEFAULT
            )
            is True
        )

        production_link = next(l for l in avail_links if l["title"] == "Production")
        assert production_link.get("rel") == "self"
        assert (
            production_link.get("properties", {}).get(
                OPDSCatalog.PALACE_PROPERTIES_DEFAULT
            )
            is True
        )

    def test_sort_links_preserve_availability(self):
        """Sort facet links include availability when it was given."""
        cat = self._make_catalog(order_str="name", availability_str="all")
        sort_links = cat["facets"][0]["links"]
        for link in sort_links:
            assert (
                "availability=all" in link["href"]
            )  # no encoding needed for plain values

    def test_sort_links_omit_availability_when_default(self):
        """Sort facet links omit availability when it was not in the request."""
        cat = self._make_catalog(order_str="name", availability_str=None)
        sort_links = cat["facets"][0]["links"]
        for link in sort_links:
            assert "availability" not in link["href"]

    def test_avail_links_preserve_order(self):
        """Availability facet links include order when it was given."""
        cat = self._make_catalog(order_str="name", availability_str="all")
        avail_links = cat["facets"][1]["links"]
        for link in avail_links:
            assert "order=name" in link["href"]

    def test_avail_links_omit_order_when_default(self):
        """Availability facet links omit order when it was not in the request."""
        cat = self._make_catalog(order_str=None, availability_str="hidden")
        avail_links = cat["facets"][1]["links"]
        for link in avail_links:
            assert "order" not in link["href"]

    def test_size_not_in_facet_links(self):
        """Facet links do not include offset or size (reset pagination)."""
        cat = self._make_catalog(order_str="name", availability_str="production")
        all_links = cat["facets"][0]["links"] + cat["facets"][1]["links"]
        for link in all_links:
            assert "offset" not in link["href"]
            assert "size" not in link["href"]

    def test_commas_not_percent_encoded_in_facet_links(self):
        """Commas in availability values are preserved literally, not encoded as %2C."""
        cat = self._make_catalog(order_str="name", availability_str="production,hidden")
        sort_links = cat["facets"][0]["links"]
        for link in sort_links:
            assert "availability=production,hidden" in link["href"]
            assert "%2C" not in link["href"]

    def test_paired_sort_links_have_group_property(self):
        """Paired sort variants share a group property; singletons omit it."""
        cat = self._make_catalog()
        sort_links = cat["facets"][0]["links"]
        links_by_value = {
            l["properties"][OPDSCatalog.FACET_VALUE_PROPERTY]: l for l in sort_links
        }

        # Paired variants share their group name.
        assert (
            links_by_value["modified"]["properties"][OPDSCatalog.FACET_GROUP_PROPERTY]
            == "modified"
        )
        assert (
            links_by_value["modified-asc"]["properties"][
                OPDSCatalog.FACET_GROUP_PROPERTY
            ]
            == "modified"
        )
        assert (
            links_by_value["name"]["properties"][OPDSCatalog.FACET_GROUP_PROPERTY]
            == "name"
        )
        assert (
            links_by_value["name-desc"]["properties"][OPDSCatalog.FACET_GROUP_PROPERTY]
            == "name"
        )

        # Singleton has no group property.
        assert (
            OPDSCatalog.FACET_GROUP_PROPERTY
            not in links_by_value["natural"]["properties"]
        )

    def test_availability_links_have_no_group_property(self):
        """Availability facet links do not carry a group property."""
        cat = self._make_catalog()
        avail_links = cat["facets"][1]["links"]
        for link in avail_links:
            assert OPDSCatalog.FACET_GROUP_PROPERTY not in link["properties"]

    def test_facet_group_metadata_has_param(self):
        """Each facet group's metadata carries the query parameter name."""
        cat = self._make_catalog()
        sort_meta = cat["facets"][0]["metadata"]
        avail_meta = cat["facets"][1]["metadata"]
        assert sort_meta[OPDSCatalog.FACET_PARAM_PROPERTY] == "order"
        assert avail_meta[OPDSCatalog.FACET_PARAM_PROPERTY] == "availability"

    def test_facet_links_have_value_property(self):
        """Every facet link carries the query-parameter value it represents."""
        cat = self._make_catalog()
        sort_links = cat["facets"][0]["links"]
        avail_links = cat["facets"][1]["links"]

        sort_values = [
            l["properties"][OPDSCatalog.FACET_VALUE_PROPERTY] for l in sort_links
        ]
        assert sort_values == [f.value for f in OrderFacet.advertised_facets()]

        avail_values = [
            l["properties"][OPDSCatalog.FACET_VALUE_PROPERTY] for l in avail_links
        ]
        assert avail_values == [f.value for f in AvailabilityFacet.advertised_facets()]


class TestOPDSCatalog:
    def test_strftime_format(self):
        """_strftime produces dates in the expected ISO 8601 format: YYYY-MM-DDTHH:MM:SSZ."""
        aware = datetime.datetime(2024, 3, 15, 10, 30, 0, tzinfo=datetime.UTC)
        result = OPDSCatalog._strftime(aware)
        # Raises ValueError if the format doesn't match exactly.
        datetime.datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ")

    def mock_url_for(self, route, uuid, **kwargs):
        """A simple replacement for url_for that doesn't require an
        application context.
        """
        return f"http://{route}/{uuid}"

    def test_library_catalogs(self, db: DatabaseTransactionFixture):
        l1 = db.library("The New York Public Library")
        l2 = db.library("Brooklyn Public Library")

        class TestAnnotator:
            def annotate_catalog(self, catalog_obj, live=True):
                catalog_obj.catalog["metadata"][
                    "random"
                ] = "Random text inserted by annotator."

        # This template will be used to construct a web client link
        # for each library.
        template = "http://web/{uuid}"
        ConfigurationSetting.sitewide(
            db.session, Configuration.WEB_CLIENT_URL
        ).value = template

        catalog = OPDSCatalog(
            db.session,
            "A Catalog!",
            "http://url/",
            [l1, l2],
            TestAnnotator(),
            url_for=self.mock_url_for,
        )
        catalog = str(catalog)
        parsed = json.loads(catalog)

        # The catalog is labeled appropriately.
        assert parsed["metadata"]["title"] == "A Catalog!"
        [self_link] = parsed["links"]
        assert self_link["href"] == "http://url/"
        assert self_link["rel"] == "self"

        # The annotator modified the catalog in passing.
        assert parsed["metadata"]["random"] == "Random text inserted by annotator."

        # Each library became a catalog in the catalogs collection.
        assert [x["metadata"]["title"] for x in parsed["catalogs"]] == [
            l1.name,
            l2.name,
        ]

        # Each library has a link to its web catalog.
        l1_links, l2_links = (library["links"] for library in parsed["catalogs"])
        [l1_web] = [link["href"] for link in l1_links if link["type"] == "text/html"]
        assert template.replace("{uuid}", l1.internal_urn) == l1_web

        [l2_web] = [link["href"] for link in l2_links if link["type"] == "text/html"]
        assert template.replace("{uuid}", l2.internal_urn) == l2_web

    def test_large_feeds_treated_differently(self, db: DatabaseTransactionFixture):
        # The libraries in large feeds are converted to JSON in ways
        # that omit large chunks of data such as inline logos.

        # In this test, a feed with 2 or more items is considered
        # 'large'. Any smaller feed is considered 'small'.
        setting = ConfigurationSetting.sitewide(
            db.session, Configuration.LARGE_FEED_SIZE
        )
        setting.value = 2

        class Mock(OPDSCatalog):
            def library_catalog(*args, **kwargs):
                # Every time library_catalog is called, record whether
                # we were asked to include logo and service area.
                return kwargs["include_logo"], kwargs["include_service_area"]

        # Every item in the large feed resulted in a call with
        # include_logo=False and include_service_areas=False.
        large_feed = Mock(db.session, "title", "url", ["it's", "large"])
        large_catalog = large_feed.catalog["catalogs"]
        assert large_catalog == [(False, False), (False, False)]

        # Every item in the small feed resulted in a call with
        # include_logo=True and include_service_areas=True.
        small_feed = Mock(db.session, "title", "url", ["small"])
        small_catalog = small_feed.catalog["catalogs"]
        assert small_catalog == [(True, True)]

        # Make it so even a feed with one item is 'large'.
        setting.value = 1
        small_feed = Mock(db.session, "title", "url", ["small"])
        small_catalog = small_feed.catalog["catalogs"]
        assert small_catalog == [(False, False)]

        # Try it with a query that returns no results. No catalogs
        # are included at all.
        small_feed = Mock(db.session, "title", "url", db.session.query(Library))
        small_catalog = small_feed.catalog["catalogs"]
        assert small_catalog == []

    def test_feed_is_large(self, db: DatabaseTransactionFixture):
        # Verify that the _feed_is_large helper method
        # works whether it's given a Python list or a SQLAlchemy query.
        setting = ConfigurationSetting.sitewide(
            db.session, Configuration.LARGE_FEED_SIZE
        )
        setting.value = 2
        m = OPDSCatalog._feed_is_large
        query = db.session.query(Library)

        # There are no libraries, and the limit is 2, so a feed of libraries would not be large.
        assert query.count() == 0
        assert m(db.session, query) is False

        # Make some libraries, and the feed becomes large.
        [db.library() for x in range(2)]
        assert m(db.session, query) is True

        # It also works with a list.
        assert m(db.session, [1, 2]) is True
        assert m(db.session, [1]) is False

    def test_library_catalog(self, db: DatabaseTransactionFixture):
        class Mock(OPDSCatalog):
            """An OPDSCatalog that instruments calls to _hyperlink_args."""

            hyperlinks = []

            @classmethod
            def _hyperlink_args(cls, hyperlink):
                cls.hyperlinks.append(hyperlink)
                return OPDSCatalog._hyperlink_args(hyperlink)

        library = db.library(
            "The New York Public Library", focus_areas=[db.new_york_city]
        )
        library.urn = "123-abc"
        library.description = "It's a wonderful library."
        library.opds_url = "https://opds/"
        library.web_url = "https://nypl.org/"
        library.authentication_url = "http://authdocument/"
        library.logo_url = "http://logo-url/"

        # This email address is a secret between the library and the
        # registry.
        private_hyperlink, ignore = library.set_hyperlink(
            Hyperlink.INTEGRATION_CONTACT_REL, "mailto:secret@library.org"
        )

        # This email address is intended for public consumption.
        public_hyperlink, ignore = library.set_hyperlink(
            Hyperlink.HELP_REL, "mailto:help@library.org"
        )

        catalog = Mock.library_catalog(
            library,
            url_for=self.mock_url_for,
            web_client_uri_template="http://web/{uuid}",
            distance=14244,
            include_service_area=True,
        )
        metadata = catalog["metadata"]
        assert metadata["title"] == library.name
        assert metadata["id"] == library.internal_urn
        assert metadata["description"] == library.description

        # The distance between the current location and the edge of
        # the library's service area is published as 'schema:distance'
        # and also (for backwards compatibility) as 'distance'
        for key in ("schema:distance", "distance"):
            assert metadata[key] == "14 km."

        # The library's updated timestamp is published as 'modified'
        # and also (for backwards compatibility) as 'updated'.
        timestamp = OPDSCatalog._strftime(library.timestamp)
        for key in ("modified", "updated"):
            assert metadata[key] == timestamp

        # If the library's service area is easy to explain in human-friendly
        # terms, it is explained in 'schema:areaServed'.
        assert metadata["schema:areaServed"] == "New York, NY"

        # That also means the library will be given an OPDS subject
        # corresponding to its type.
        [subject] = metadata["subject"]
        assert LibraryType.SCHEME_URI == subject["scheme"]
        assert LibraryType.LOCAL == subject["code"]
        assert LibraryType.NAME_FOR_CODE[LibraryType.LOCAL] == subject["name"]

        [
            authentication_url,
            web_alternate,
            help,
            eligibility,
            focus,
            opds_self,
            web_self,
        ] = sorted(
            catalog["links"], key=lambda x: (x.get("rel", ""), x.get("type", ""))
        )
        [logo] = catalog["images"]

        assert help["href"] == "mailto:help@library.org"
        assert help["rel"] == Hyperlink.HELP_REL

        assert web_alternate["href"] == library.web_url
        assert web_alternate["rel"] == "alternate"
        assert web_alternate["type"] == "text/html"

        assert opds_self["href"] == library.opds_url
        assert opds_self["rel"] == OPDSCatalog.CATALOG_REL
        assert opds_self["type"] == OPDSCatalog.OPDS_1_TYPE

        assert web_self["href"] == "http://web/%s" % library.internal_urn
        assert web_self["rel"] == "self"
        assert web_self["type"] == "text/html"

        assert (
            eligibility["href"]
            == "http://library_eligibility/%s" % library.internal_urn
        )
        assert eligibility["rel"] == OPDSCatalog.ELIGIBILITY_REL
        assert eligibility["type"] == "application/geo+json"

        assert focus["href"] == "http://library_focus/%s" % library.internal_urn
        assert focus["rel"] == OPDSCatalog.FOCUS_REL
        assert focus["type"] == "application/geo+json"

        assert logo["rel"] == "http://opds-spec.org/image/thumbnail"
        assert logo["type"] == "image/png"
        assert logo["href"] == "http://logo-url/"

        assert authentication_url["href"] == library.authentication_url
        assert "rel" not in authentication_url
        assert authentication_url["type"] == AuthenticationDocument.MEDIA_TYPE
        # The public Hyperlink was passed into _hyperlink_args,
        # which made it show up in the list of links.
        #
        # The private Hyperlink was not passed in.
        assert Mock.hyperlinks == [public_hyperlink]
        Mock.hyperlinks = []

        # If library_catalog is called with include_private_information=True,
        # both Hyperlinks are passed into _hyperlink_args.
        catalog = Mock.library_catalog(
            library, include_private_information=True, url_for=self.mock_url_for
        )
        assert set(Mock.hyperlinks) == {public_hyperlink, private_hyperlink}

        # If library_catalog is called with include_logo=False,
        # the (potentially large) inline logo is omitted,
        # even though it was included before.
        catalog = Mock.library_catalog(
            library, include_logo=False, url_for=self.mock_url_for
        )
        relations = [x.get("rel") for x in catalog["links"]]
        assert OPDSCatalog.THUMBNAIL_REL not in relations

        # If library_catalog is called with
        # include_service_area=False, information about the library's
        # service area is not included in the library's OPDS entry.
        catalog = Mock.library_catalog(
            library, url_for=self.mock_url_for, include_service_area=False
        )
        for missing_key in (
            "schema:areaServed",
            "schema:distance",
            "distance",
            "subject",
        ):
            assert missing_key not in catalog["metadata"]

        # The same holds true if service area information is not available.
        library.service_areas = []
        catalog = Mock.library_catalog(
            library, url_for=self.mock_url_for, include_service_area=True
        )
        for missing_key in (
            "schema:areaServed",
            "schema:distance",
            "distance",
            "subject",
        ):
            assert missing_key not in catalog["metadata"]

        # Try again by adding a library logo_url,
        # the image href should now be the url.
        # Even if include_logos is False
        library.logo_url = "http://logourl"
        catalog = Mock.library_catalog(
            library, include_logo=False, url_for=self.mock_url_for
        )
        assert catalog["images"][0]["href"] == "http://logourl"

    def test__hyperlink_args(self, db: DatabaseTransactionFixture):
        """Verify that _hyperlink_args generates arguments appropriate
        for an OPDS 2 link.
        """
        m = OPDSCatalog._hyperlink_args

        library = db.library()
        hyperlink, is_new = library.set_hyperlink("some-rel", None)

        # If there's not enough information to make a link,
        # _hyperlink_args returns None.
        assert m(None) is None
        assert m(hyperlink) is None

        # Now there's enough for a link, but there's no Validation.
        hyperlink.href = "a url"
        assert m(hyperlink) == dict(href=hyperlink.href, rel=hyperlink.rel)

        # Create a Validation.
        validation, is_new = create(db.session, Validation)
        hyperlink.resource.validation = validation

        def assert_reservation_status(expect):
            args = m(hyperlink)
            assert expect == args["properties"][Validation.STATUS_PROPERTY]

        # Validation in progress
        assert_reservation_status(Validation.IN_PROGRESS)

        # Validation has expired
        validation.started_at = utc_now() - datetime.timedelta(days=365)
        assert_reservation_status(Validation.INACTIVE)

        # Validation has been confirmed
        validation.success = True
        assert_reservation_status(Validation.CONFIRMED)

        # If for some reason the Resource is removed from the Hyperlink,
        # _hyperlink_args stops working.
        hyperlink.resource = None
        assert m(hyperlink) is None


class TestOpdsMediaTypeChecks:
    @pytest.mark.parametrize(
        "media_type, expect_is_opds1, expect_is_opds2",
        [
            pytest.param(
                "application/atom+xml;profile=opds-catalog;kind=acquisition",
                True,
                False,
                id="opds1-acquisition",
            ),
            pytest.param(
                "application/atom+xml;kind=acquisition;profile=opds-catalog",
                True,
                False,
                id="opds1_acquisition-different-order",
            ),
            pytest.param(
                "application/atom+xml;profile=opds-catalog;kind=acquisition;api-version=1",
                True,
                False,
                id="opds1-acquisition-apiv1",
            ),
            pytest.param(
                "application/atom+xml;api-version=1;kind=acquisition;profile=opds-catalog",
                True,
                False,
                id="opds1_acquisition_apiv1-different-order",
            ),
            pytest.param(
                "application/atom+xml;api-version=2;kind=acquisition;profile=opds-catalog",
                True,
                False,
                id="opds1-acquisition-apiv2",
            ),
            pytest.param(
                "application/atom+xml;profile=opds-catalog;kind=navigation",
                False,
                False,
                id="opds1-navigation",
            ),
            pytest.param(
                "application/opds+json;api-version=1", False, True, id="opds2-apiv1"
            ),
            pytest.param(
                "application/opds+json;api-version=2", False, True, id="opds2-apiv2"
            ),
            pytest.param("application/epub+zip", False, False, id="epub+zip"),
            pytest.param("application/json", False, False, id="application-json"),
            pytest.param("", False, False, id="empty-string"),
            pytest.param(None, False, False, id="none-value"),
        ],
    )
    def test_opds_catalog_types(
        self, media_type: str | None, expect_is_opds1: bool, expect_is_opds2: bool
    ) -> None:
        is_opds1 = OPDSCatalog.is_opds1_type(media_type)
        is_opds2 = OPDSCatalog.is_opds2_type(media_type)
        is_opds_catalog_type = OPDSCatalog.is_opds_type(media_type)

        assert is_opds1 == expect_is_opds1
        assert is_opds2 == expect_is_opds2
        assert is_opds_catalog_type == (expect_is_opds1 or expect_is_opds2)
