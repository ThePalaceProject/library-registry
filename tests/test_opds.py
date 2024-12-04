from __future__ import annotations

import datetime
import json

import pytest

from authentication_document import AuthenticationDocument
from config import Configuration
from model import (
    ConfigurationSetting,
    Hyperlink,
    Library,
    LibraryType,
    Validation,
    create,
)
from opds import OPDSCatalog
from tests.fixtures.database import DatabaseTransactionFixture


class TestOPDSCatalog:
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
        validation.started_at = datetime.datetime.utcnow() - datetime.timedelta(
            days=365
        )
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
