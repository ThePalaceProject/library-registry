from nose.tools import (
    eq_,
    set_trace,
)
import json

from . import (
    DatabaseTest,
)

from opds import OPDSCatalog

class TestOPDSCatalog(DatabaseTest):

    def test_library_catalogs(self):
        l1 = self._library("The New York Public Library")
        l2 = self._library("Brooklyn Public Library")
        class TestAnnotator(object):
            def annotate_catalog(self, catalog_obj):
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

        library = self._library("The New York Public Library")
        library.urn = "123-abc"
        library.description = "It's a wonderful library."
        library.opds_url = "https://opds/"
        library.web_url = "https://nypl.org/"
        library.logo = "Fake logo"
        
        catalog = OPDSCatalog.library_catalog(library)
        metadata = catalog['metadata']
        eq_(library.name, metadata['title'])
        eq_(library.urn_uri, metadata['id'])
        eq_(library.description, metadata['description'])

        eq_(metadata['updated'], OPDSCatalog._strftime(library.timestamp))

        [web, opds] = sorted(catalog['links'], key=lambda x: x['rel'])
        [logo] = catalog['images']

        eq_(library.web_url, web['href'])
        eq_("alternate", web['rel'])
        eq_("text/html", web['type'])

        eq_(library.opds_url, opds['href'])
        eq_(OPDSCatalog.CATALOG_REL, opds['rel'])
        eq_(OPDSCatalog.OPDS_1_TYPE, opds['type'])

        eq_(library.logo_data_uri, logo['href'])
        eq_(OPDSCatalog.THUMBNAIL_REL, logo['rel'])
        eq_("image/png", logo['type'])

