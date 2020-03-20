from nose.tools import (
    eq_,
    set_trace,
)
import datetime
import json

from . import (
    DatabaseTest,
)

from authentication_document import AuthenticationDocument
from config import Configuration
from model import (
    create,
    ConfigurationSetting,
    Hyperlink,
    Library,
    Validation,
)
from opds import OPDSCatalog

class TestOPDSCatalog(DatabaseTest):

    def mock_url_for(self, route, uuid, **kwargs):
        """A simple replacement for url_for that doesn't require an
        application context.
        """
        return "http://%s/%s" % (route, uuid)

    def test_library_catalogs(self):
        l1 = self._library("The New York Public Library")
        l2 = self._library("Brooklyn Public Library")
        class TestAnnotator(object):
            def annotate_catalog(self, catalog_obj, live=True):
                catalog_obj.catalog['metadata']['random'] = "Random text inserted by annotator."

        catalog = OPDSCatalog(
            self._db, "A Catalog!", "http://url/", [l1, l2],
            TestAnnotator(), url_for=self.mock_url_for
        )
        catalog = unicode(catalog)
        parsed = json.loads(catalog)

        # The catalog is labeled appropriately.
        eq_("A Catalog!", parsed['metadata']['title'])
        [self_link] = parsed['links']
        eq_("http://url/", self_link['href'])
        eq_("self", self_link['rel'])

        # The annotator modified the catalog in passing.
        eq_("Random text inserted by annotator.", parsed['metadata']['random'])

        # Each library became a catalog in the catalogs collection.
        eq_([l1.name, l2.name], [x['metadata']['title'] for x in parsed['catalogs']])

    def test_large_feeds_treated_differently(self):
        # The libraries in large feeds are converted to JSON in ways
        # that omit large chunks of data such as inline logos.

        # In this test, a feed with 2 or more items is considered
        # 'large'. Any smaller feed is considered 'small'.
        setting = ConfigurationSetting.sitewide(
            self._db, Configuration.LARGE_FEED_SIZE
        )
        setting.value = 2

        class Mock(OPDSCatalog):
            def library_catalog(*args, **kwargs):
                # Every time library_catalog is called, record whether
                # we were asked to include a logo.
                return kwargs['include_logo']

        # Every item in the large feed resulted in a call with
        # include_logo=False.
        large_feed = Mock(self._db, "title", "url", ["it's", "large"])
        large_catalog = large_feed.catalog['catalogs']
        eq_([False, False], large_catalog)

        # Every item in the large feed resulted in a call with
        # include_logo=True.
        small_feed = Mock(self._db, "title", "url", ["small"])
        small_catalog = small_feed.catalog['catalogs']
        eq_([True], small_catalog)

        # Make it so even a feed with one item is 'large'.
        setting.value = 1
        small_feed = Mock(self._db, "title", "url", ["small"])
        small_catalog = small_feed.catalog['catalogs']
        eq_([False], small_catalog)

        # Try it with a query that returns no results. No catalogs
        # are included at all.
        small_feed = Mock(self._db, "title", "url", self._db.query(Library))
        small_catalog = small_feed.catalog['catalogs']
        eq_([], small_catalog)

    def _test_feed_is_large(self):
        # Verify that the _feed_is_large helper method
        # works whether it's given a Python list or a SQLAlchemy query.
        setting = ConfigurationSetting.sitewide(
            self._db, Configuration.LARGE_FEED_SIZE
        )
        setting.value = 2
        m = OPDSCatalog.feed_is_large
        list = [1,2,3]
        query = self._db.query(libraries)
        eq_(0, query.count())

        eq_(True, m(self._db, query))
        eq_(False, m(self._db, query))

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

        # This email address is a secret between the library and the
        # registry.
        private_hyperlink, ignore = library.set_hyperlink(
            Hyperlink.INTEGRATION_CONTACT_REL,
            "mailto:secret@library.org"
        )

        # This email address is intended for public consumption.
        public_hyperlink, ignore = library.set_hyperlink(
            Hyperlink.HELP_REL,
            "mailto:help@library.org"
        )

        ConfigurationSetting.sitewide(
            self._db, Configuration.WEB_CLIENT_URL).value = "http://web/{uuid}"

        catalog = Mock.library_catalog(library, url_for=self.mock_url_for)
        metadata = catalog['metadata']
        eq_(library.name, metadata['title'])
        eq_(library.internal_urn, metadata['id'])
        eq_(library.description, metadata['description'])

        eq_(metadata['updated'], OPDSCatalog._strftime(library.timestamp))

        [authentication_url, web_alternate, help, eligibility, focus, opds_self, web_self] = sorted(catalog['links'], key=lambda x: (x.get('rel', ''), x.get('type', '')))
        [logo] = catalog['images']

        eq_("mailto:help@library.org", help['href'])
        eq_(Hyperlink.HELP_REL, help['rel'])

        eq_(library.web_url, web_alternate['href'])
        eq_("alternate", web_alternate['rel'])
        eq_("text/html", web_alternate['type'])

        eq_(library.opds_url, opds_self['href'])
        eq_(OPDSCatalog.CATALOG_REL, opds_self['rel'])
        eq_(OPDSCatalog.OPDS_1_TYPE, opds_self['type'])

        eq_("http://web/%s" % library.internal_urn, web_self['href'])
        eq_("self", web_self['rel'])
        eq_("text/html", web_self['type'])

        eq_("http://library_eligibility/%s" % library.internal_urn,
            eligibility['href'])
        eq_(OPDSCatalog.ELIGIBILITY_REL, eligibility['rel'])
        eq_("application/geo+json", eligibility['type'])

        eq_("http://library_focus/%s" % library.internal_urn,
            focus['href'])
        eq_(OPDSCatalog.FOCUS_REL, focus['rel'])
        eq_("application/geo+json", focus['type'])

        eq_(library.logo, logo['href'])
        eq_(OPDSCatalog.THUMBNAIL_REL, logo['rel'])
        eq_("image/png", logo['type'])

        eq_(library.authentication_url, authentication_url['href'])
        assert 'rel' not in authentication_url
        eq_(AuthenticationDocument.MEDIA_TYPE, authentication_url['type'])
        # The public Hyperlink was passed into _hyperlink_args,
        # which made it show up in the list of links.
        #
        # The private Hyperlink was not passed in.
        eq_([public_hyperlink], Mock.hyperlinks)
        Mock.hyperlinks = []

        # If library_catalog is called with include_private_information=True,
        # both Hyperlinks are passed into _hyperlink_args.
        catalog = Mock.library_catalog(
            library, include_private_information=True,
            url_for=self.mock_url_for
        )
        eq_(set([public_hyperlink, private_hyperlink]), set(Mock.hyperlinks))

        # If library_catalog is passed with include_logo=False,
        # the (potentially large) inline logo is omitted, 
        # even though it was included before.
        catalog = Mock.library_catalog(
            library, include_logo=False, 
            url_for=self.mock_url_for
        )
        relations = [x.get('rel') for x in catalog['links']]
        assert OPDSCatalog.THUMBNAIL_REL not in relations


    def test__hyperlink_args(self):
        """Verify that _hyperlink_args generates arguments appropriate
        for an OPDS 2 link.
        """
        m = OPDSCatalog._hyperlink_args

        library = self._library()
        hyperlink, is_new = library.set_hyperlink("some-rel", None)

        # If there's not enough information to make a link,
        # _hyperlink_args returns None.
        eq_(None, m(None))
        eq_(None, m(hyperlink))

        # Now there's enough for a link, but there's no Validation.
        hyperlink.href = "a url"
        eq_(dict(href=hyperlink.href, rel=hyperlink.rel), m(hyperlink))

        # Create a Validation.
        validation, is_new = create(self._db, Validation)
        hyperlink.resource.validation = validation

        def assert_reservation_status(expect):
            args = m(hyperlink)
            eq_(args['properties'][Validation.STATUS_PROPERTY], expect)

        # Validation in progress
        assert_reservation_status(Validation.IN_PROGRESS)

        # Validation has expired
        validation.started_at = datetime.datetime.utcnow()-datetime.timedelta(
            days=365
        )
        assert_reservation_status(Validation.INACTIVE)

        # Validation has been confirmed
        validation.success = True
        assert_reservation_status(Validation.CONFIRMED)

        # If for some reason the Resource is removed from the Hyperlink,
        # _hyperlink_args stops working.
        hyperlink.resource = None
        eq_(None, m(hyperlink))
