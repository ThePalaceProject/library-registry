from nose.tools import set_trace
import json

from model import (
    Hyperlink,
    Validation,
)

class Annotator(object):

    def annotate_feed(self, feed):
        pass

class OPDSCatalog(object):
    """Represents an OPDS 2 Catalog Document.
    https://github.com/opds-community/opds-revision/blob/master/opds-2.0.md

    This document may stand on its own, or be contained within another
    OPDS 2 Catalog Document in a collection with the "catalogs" role.

    Within the "catalogs" role, metadata and the navigation collection role
    have the same semantics as in the overall OPDS 2 Catalog spec.
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

    def __init__(self, _db, title, url, libraries, annotator=None,
                 live=True):
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
        annotator.annotate_catalog(self, live=live)

    @classmethod
    def library_catalog(cls, library, distance=None,
                        include_private_information=False):

        """Create an OPDS catalog for a library.

        :param include_private_information: If this is True, the
        consumer of this OPDS catalog is expected to be the library
        whose catalog it is. Private information such as the point of
        contact for integration problems will be included, where it
        normally wouldn't be.
        """
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
                                     href=library.logo,
                                     type="image/png")

        for hyperlink in library.hyperlinks:
            if (not include_private_information and hyperlink.rel in
                Hyperlink.PRIVATE_RELS):
                continue
            args = cls._hyperlink_args(hyperlink)
            if not args:
                # Not enough information to create a link.
                continue
            cls.add_link_to_catalog(
                catalog, **args
            )
        return catalog

    @classmethod
    def _hyperlink_args(cls, hyperlink):
        """Turn a Hyperlink into a dictionary of arguments that can
        be turned into an OPDS 2 link.
        """
        if not hyperlink:
            return None
        resource = hyperlink.resource
        if not resource:
            return None
        href = resource.href
        if not href:
            return None
        args = dict(rel=hyperlink.rel, href=href)

        # If there was ever an attempt to validate this Hyperlink,
        # explain the status of that attempt.
        properties = {}
        validation = resource.validation
        if validation:
            if validation.success:
                status = Validation.CONFIRMED
            elif validation.active:
                status = Validation.IN_PROGRESS
            else:
                status = Validation.INACTIVE
            properties[Validation.STATUS_PROPERTY] = status
        if properties:
            args['properties'] = properties
        return args

    def __unicode__(self):
        if self.catalog is None:
            return None

        return json.dumps(self.catalog)




