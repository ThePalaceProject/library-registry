import datetime
import json

from library_registry.authentication_document import AuthenticationDocument
from library_registry.config import Configuration
from library_registry.model import (
    create,
    ConfigurationSetting,
    Hyperlink,
    Library,
    Validation,
)
from library_registry.opds import OPDSCatalog
from . import DatabaseTest


class TestOPDSCatalog(DatabaseTest):

    def mock_url_for(self, route, uuid, **kwargs):
        """A simple replacement for url_for that doesn't require an application context"""
        return "http://%s/%s" % (route, uuid)

    def test_library_catalogs(self):
        l1 = self._library("The New York Public Library")
        l2 = self._library("Brooklyn Public Library")

        class TestAnnotator(object):
            def annotate_catalog(self, catalog_obj, live=True):
                catalog_obj.catalog['metadata']['random'] = "Random text inserted by annotator."

        # This template will be used to construct a web client link for each library.
        template = "http://web/{uuid}"
        ConfigurationSetting.sitewide(self._db, Configuration.WEB_CLIENT_URL).value = template

        catalog = OPDSCatalog(
            self._db, "A Catalog!", "http://url/", [l1, l2],
            TestAnnotator(), url_for=self.mock_url_for
        )
        catalog = str(catalog)
        parsed = json.loads(catalog)

        # The catalog is labeled appropriately.
        assert parsed['metadata']['title'] == "A Catalog!"
        [self_link] = parsed['links']
        assert self_link['href'] == "http://url/"
        assert self_link['rel'] == "self"

        # The annotator modified the catalog in passing.
        assert parsed['metadata']['random'] == "Random text inserted by annotator."

        # Each library became a catalog in the catalogs collection.
        assert [x['metadata']['title'] for x in parsed['catalogs']] == [l1.name, l2.name]

        # Each library has a link to its web catalog.
        l1_links, l2_links = [library['links'] for library in parsed['catalogs']]
        [l1_web] = [link['href'] for link in l1_links if link['type'] == 'text/html']
        assert l1_web == template.replace("{uuid}", l1.internal_urn)

        [l2_web] = [link['href'] for link in l2_links if link['type'] == 'text/html']
        assert l2_web == template.replace("{uuid}", l2.internal_urn)

    def test_large_feeds_treated_differently(self):
        # The libraries in large feeds are converted to JSON in ways
        # that omit large chunks of data such as inline logos.

        # In this test, a feed with 2 or more items is considered
        # 'large'. Any smaller feed is considered 'small'.
        setting = ConfigurationSetting.sitewide(self._db, Configuration.LARGE_FEED_SIZE)
        setting.value = 2

        class Mock(OPDSCatalog):
            def library_catalog(*args, **kwargs):
                # Every time library_catalog is called, record whether
                # we were asked to include a logo.
                return kwargs['include_logo']

        # Every item in the large feed resulted in a call with include_logo=False.
        large_feed = Mock(self._db, "title", "url", ["it's", "large"])
        large_catalog = large_feed.catalog['catalogs']
        assert large_catalog == [False, False]

        # Every item in the large feed resulted in a call with include_logo=True.
        small_feed = Mock(self._db, "title", "url", ["small"])
        small_catalog = small_feed.catalog['catalogs']
        assert small_catalog == [True]

        # Make it so even a feed with one item is 'large'.
        setting.value = 1
        small_feed = Mock(self._db, "title", "url", ["small"])
        small_catalog = small_feed.catalog['catalogs']
        assert small_catalog == [False]

        # Try it with a query that returns no results. No catalogs are included at all.
        small_feed = Mock(self._db, "title", "url", self._db.query(Library))
        small_catalog = small_feed.catalog['catalogs']
        assert small_catalog == []

    def test_feed_is_large(self):
        # Verify that the _feed_is_large helper method
        # works whether it's given a Python list or a SQLAlchemy query.
        setting = ConfigurationSetting.sitewide(self._db, Configuration.LARGE_FEED_SIZE)
        setting.value = 2
        m = OPDSCatalog._feed_is_large
        query = self._db.query(Library)

        # There are no libraries, and the limit is 2, so a feed of libraries would not be large.
        assert query.count() == 0
        assert m(self._db, query) is False

        # Make some libraries, and the feed becomes large.
        [self._library() for x in range(2)]
        assert m(self._db, query) is True

        # It also works with a list.
        assert m(self._db, [1, 2]) is True
        assert m(self._db, [1]) is False

    def test_library_catalog(self):

        class Mock(OPDSCatalog):
            """An OPDSCatalog that instruments calls to _hyperlink_args."""
            hyperlinks = []

            @classmethod
            def _hyperlink_args(cls, hyperlink):
                cls.hyperlinks.append(hyperlink)
                return OPDSCatalog._hyperlink_args(hyperlink)

        library = self._library("The New York Public Library")
        library.urn = "123-abc"
        library.description = "It's a wonderful library."
        library.opds_url = "https://opds/"
        library.web_url = "https://nypl.org/"
        library.logo = "Fake logo"
        library.authentication_url = "http://authdocument/"

        # This email address is a secret between the library and the registry.
        (private_hyperlink, ignore) = library.set_hyperlink(Hyperlink.INTEGRATION_CONTACT_REL,
                                                            "mailto:secret@library.org")

        # This email address is intended for public consumption.
        public_hyperlink, ignore = library.set_hyperlink(Hyperlink.HELP_REL, "mailto:help@library.org")

        catalog = Mock.library_catalog(library, url_for=self.mock_url_for, web_client_uri_template="http://web/{uuid}")
        metadata = catalog['metadata']
        assert metadata['title'] == library.name
        assert metadata['id'] == library.internal_urn
        assert metadata['description'] == library.description

        assert metadata['updated'] == OPDSCatalog._strftime(library.timestamp)

        (authentication_url, web_alternate, help, eligibility, focus, opds_self, web_self) = sorted(
            catalog['links'], key=lambda x: (x.get('rel', ''), x.get('type', ''))
        )
        [logo] = catalog['images']

        assert help['href'] == "mailto:help@library.org"
        assert help['rel'] == Hyperlink.HELP_REL

        assert web_alternate['href'] == library.web_url
        assert web_alternate['rel'] == "alternate"
        assert web_alternate['type'] == "text/html"

        assert opds_self['href'] == library.opds_url
        assert opds_self['rel'] == OPDSCatalog.CATALOG_REL
        assert opds_self['type'] == OPDSCatalog.OPDS_1_TYPE

        assert web_self['href'] == "http://web/%s" % library.internal_urn
        assert web_self['rel'] == "self"
        assert web_self['type'] == "text/html"

        assert eligibility['href'] == "http://library_eligibility/%s" % library.internal_urn
        assert eligibility['rel'] == OPDSCatalog.ELIGIBILITY_REL
        assert eligibility['type'] == "application/geo+json"

        assert focus['href'] == "http://library_focus/%s" % library.internal_urn
        assert focus['rel'] == OPDSCatalog.FOCUS_REL
        assert focus['type'] == "application/geo+json"

        assert logo['href'] == library.logo
        assert logo['rel'] == OPDSCatalog.THUMBNAIL_REL
        assert logo['type'] == "image/png"

        assert authentication_url['href'] == library.authentication_url
        assert 'rel' not in authentication_url
        assert authentication_url['type'] == AuthenticationDocument.MEDIA_TYPE
        # The public Hyperlink was passed into _hyperlink_args,
        # which made it show up in the list of links.
        #
        # The private Hyperlink was not passed in.
        assert [public_hyperlink] == Mock.hyperlinks
        Mock.hyperlinks = []

        # If library_catalog is called with include_private_information=True,
        # both Hyperlinks are passed into _hyperlink_args.
        catalog = Mock.library_catalog(library, include_private_information=True, url_for=self.mock_url_for)
        assert set([public_hyperlink, private_hyperlink]) == set(Mock.hyperlinks)

        # If library_catalog is passed with include_logo=False, the (potentially large)
        # inline logo is omitted, even though it was included before.
        catalog = Mock.library_catalog(library, include_logo=False, url_for=self.mock_url_for)
        relations = [x.get('rel') for x in catalog['links']]
        assert OPDSCatalog.THUMBNAIL_REL not in relations

    def test__hyperlink_args(self):
        """Verify that _hyperlink_args generates arguments appropriate for an OPDS 2 link"""
        m = OPDSCatalog._hyperlink_args

        library = self._library()
        hyperlink, is_new = library.set_hyperlink("some-rel", None)

        # If there's not enough information to make a link, _hyperlink_args returns None.
        assert m(None) is None
        assert m(hyperlink) is None

        # Now there's enough for a link, but there's no Validation.
        hyperlink.href = "a url"
        assert m(hyperlink) == dict(href=hyperlink.href, rel=hyperlink.rel)

        # Create a Validation.
        validation, is_new = create(self._db, Validation)
        hyperlink.resource.validation = validation

        def assert_reservation_status(expect):
            args = m(hyperlink)
            assert args['properties'][Validation.STATUS_PROPERTY] == expect

        # Validation in progress
        assert_reservation_status(Validation.IN_PROGRESS)

        # Validation has expired
        validation.started_at = datetime.datetime.utcnow()-datetime.timedelta(days=365)
        assert_reservation_status(Validation.INACTIVE)

        # Validation has been confirmed
        validation.success = True
        assert_reservation_status(Validation.CONFIRMED)

        # If for some reason the Resource is removed from the Hyperlink,
        # _hyperlink_args stops working.
        hyperlink.resource = None
        assert m(hyperlink) is None
