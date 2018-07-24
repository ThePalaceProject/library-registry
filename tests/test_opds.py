from nose.tools import (
    eq_,
    set_trace,
)
import datetime
import json

from . import (
    DatabaseTest,
)

from model import (
    get_one_or_create,
    Hyperlink,
    Validation,
)
from opds import OPDSCatalog

class TestOPDSCatalog(DatabaseTest):

    def test_library_catalogs(self):
        l1 = self._library("The New York Public Library")
        l2 = self._library("Brooklyn Public Library")
        class TestAnnotator(object):
            def annotate_catalog(self, catalog_obj, live=True):
                catalog_obj.catalog['metadata']['random'] = "Random text inserted by annotator."
                
        catalog = OPDSCatalog(
            self._db, "A Catalog!", "http://url/", [l1, l2],
            TestAnnotator()
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
        
        catalog = Mock.library_catalog(library)
        metadata = catalog['metadata']
        eq_(library.name, metadata['title'])
        eq_(library.internal_urn, metadata['id'])
        eq_(library.description, metadata['description'])

        eq_(metadata['updated'], OPDSCatalog._strftime(library.timestamp))

        [web, help, opds] = sorted(catalog['links'], key=lambda x: x['rel'])
        [logo] = catalog['images']

        eq_("mailto:help@library.org", help['href'])
        eq_(Hyperlink.HELP_REL, help['rel'])

        eq_(library.web_url, web['href'])
        eq_("alternate", web['rel'])
        eq_("text/html", web['type'])

        eq_(library.opds_url, opds['href'])
        eq_(OPDSCatalog.CATALOG_REL, opds['rel'])
        eq_(OPDSCatalog.OPDS_1_TYPE, opds['type'])

        eq_(library.logo, logo['href'])
        eq_(OPDSCatalog.THUMBNAIL_REL, logo['rel'])
        eq_("image/png", logo['type'])

        # The public Hyperlink was passed into _hyperlink_args,
        # which made it show up in the list of links.
        #
        # The private Hyperlink was not passed in.
        eq_([public_hyperlink], Mock.hyperlinks)
        Mock.hyperlinks = []

        # If library_catalog is called with include_private_information=True,
        # both Hyperlinks are passed into _hyperlink_args.
        catalog = Mock.library_catalog(library, include_private_information=True)
        eq_(set([public_hyperlink, private_hyperlink]), set(Mock.hyperlinks))

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
        validation, is_new = get_one_or_create(
            self._db, Validation, resource=hyperlink.resource
        )

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
