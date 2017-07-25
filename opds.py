from nose.tools import set_trace
import json

class Annotator(object):

    def annotate_feed(self, feed):
        pass

class OPDSCatalog(object):
    """Represents an OPDS 2 Catalog document.
    https://github.com/opds-community/opds-revision/blob/master/opds-2.0.md

    Within the collection role "catalogs", metadata and the navigation
    collection role have the same semantics as in the overall OPDS 2 Catalog spec.
    """

    TIME_FORMAT = '%Y-%m-%dT%H:%M:%SZ%z'

    OPDS_TYPE = "application/opds+json"
    OPDS_1_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"

    CATALOG_REL = "http://opds-spec.org/catalog"
    THUMBNAIL_REL = "http://opds-spec.org/image/thumbnail"

    CACHE_TIME = 3600 * 12

    @classmethod
    def _strftime(cls, date):
        """
        Format a date the way Atom likes it (RFC3339?)
        """
        return date.strftime(cls.TIME_FORMAT)

    @classmethod
    def add_link_to_catalog(cls, catalog, children=None, **kwargs):
        link = dict(**kwargs)
        catalog.setdefault("links", []).append(link)

    @classmethod
    def add_image_to_catalog(cls, catalog, children=None, **kwargs):
        image = dict(**kwargs)
        catalog.setdefault("images", []).append(image)

    def __init__(self, _db, title, url, libraries, annotator=None):
        """Turn a list of libraries into a catalog."""
        if not annotator:
            annotator = Annotator()

        self.catalog = dict(metadata=dict(title=title), catalogs=[])

        self.add_link_to_catalog(self.catalog, rel="self",
                                 href=url, type=self.OPDS_TYPE)
        for library in libraries:
            if not isinstance(library, tuple):
                library = (library,)
            self.catalog["catalogs"].append(self.library_catalog(*library))
        annotator.annotate_catalog(self)

    @classmethod
    def library_catalog(cls, library, distance=None):
        metadata = dict(
            id=library.urn_uri,
            title=library.name,
            updated=cls._strftime(library.timestamp),
        )
        if distance is not None:
            metadata["distance"] = "%d km." % (distance/1000)

        if library.description:
            metadata["description"] = library.description
        catalog = dict(metadata=metadata)

        if library.opds_url:
            # TODO: Keep track of whether each library uses OPDS 1 or 2?
            cls.add_link_to_catalog(catalog, rel=cls.CATALOG_REL,
                                    href=library.opds_url,
                                    type=cls.OPDS_1_TYPE)

        if library.web_url:
            cls.add_link_to_catalog(catalog, rel="alternate",
                                    href=library.web_url,
                                    type="text/html")

        if library.logo:
            cls.add_image_to_catalog(catalog, rel=cls.THUMBNAIL_REL,
                                     href=library.logo_data_uri,
                                     type="image/png")

        return catalog

    def __unicode__(self):
        if self.catalog is None:
            return None

        return json.dumps(self.catalog)


                                   
        
