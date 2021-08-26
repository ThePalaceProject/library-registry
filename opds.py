import json

import flask
from sqlalchemy.orm import Query

from authentication_document import AuthenticationDocument
from config import Configuration
from model import ConfigurationSetting, Hyperlink, LibraryType, Session, Validation


class Annotator(object):

    def annotate_catalog(self, catalog, live=True):
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

    ELIGIBILITY_REL = "http://librarysimplified.org/rel/registry/eligibility"
    FOCUS_REL = "http://librarysimplified.org/rel/registry/focus"

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
                 live=True, url_for=None):
        """Turn a list of libraries into a catalog."""
        if not annotator:
            annotator = Annotator()

        # To save bandwidth, omit logos from large feeds. What 'large'
        # means is customizable.
        #
        # To save time, omit service area information from large feeds
        # which we know won't use it.
        include_logos = include_service_areas = not (
            self._feed_is_large(_db, libraries)
        )
        self.catalog = dict(metadata=dict(title=title), catalogs=[])

        self.add_link_to_catalog(self.catalog, rel="self",
                                 href=url, type=self.OPDS_TYPE)
        web_client_uri_template = ConfigurationSetting.sitewide(
            _db, Configuration.WEB_CLIENT_URL
        ).value
        for library in libraries:
            if not isinstance(library, tuple):
                library = (library,)
            self.catalog["catalogs"].append(
                self.library_catalog(
                    *library, url_for=url_for,
                    include_logo=include_logos,
                    web_client_uri_template=web_client_uri_template,
                    include_service_area=include_service_areas
                )
            )
        annotator.annotate_catalog(self, live=live)

    @classmethod
    def _feed_is_large(cls, _db, libraries):
        """Determine whether a prospective feed is 'large' per a sitewide setting.

        :param _db: A database session
        :param libraries: A list of libraries (or anything else that might be
            going into a feed).
        """
        large_feed_size = ConfigurationSetting.sitewide(
            _db, Configuration.LARGE_FEED_SIZE
        ).int_value
        if large_feed_size is None:
            # No limit
            return False
        if isinstance(libraries, Query):
            # This is a SQLAlchemy query.
            size = libraries.count()
        else:
            # This is something like a normal Python list.
            size = len(libraries)
        return size >= large_feed_size

    @classmethod
    def library_catalog(
            cls, library, distance=None,
            include_private_information=False,
            include_logo=True,
            url_for=None,
            web_client_uri_template=None,
            include_service_area=False,
    ):

        """Create an OPDS catalog for a library.

        :param distance: The distance, in meters, from the client's
           current location (if known) to the edge of this library's
           service area.

        :param include_private_information: If this is True, the
        consumer of this OPDS catalog is expected to be the library
        whose catalog it is. Private information such as the point of
        contact for integration problems will be included, where it
        normally wouldn't be.

        :param include_service_area: If this is True, the
            consumer of this OPDS catalog will be using information about
            the library's service area. TODO: This can be removed
            once we stop using the endpoints that just give a huge
            list of libraries.
        """
        url_for = url_for or flask.url_for

        modified = cls._strftime(library.timestamp)
        metadata = dict(
            id=library.internal_urn,
            title=library.name,
            modified=modified,
            updated=modified, # For backwards compatibility with earlier
                              # clients.
        )
        if distance is not None:
            # 'distance' for backwards compatibility.
            value = "%d km." % (distance/1000)
            for key in 'schema:distance', 'distance':
                metadata[key] = value

        if library.description:
            metadata["description"] = library.description

        if include_service_area:
            service_area_name = library.service_area_name
            if service_area_name is not None:
                metadata['schema:areaServed'] = service_area_name

            subjects = []
            for code in library.types:
                subjects.append(
                    dict(code=code, name=LibraryType.NAME_FOR_CODE[code],
                         scheme=LibraryType.SCHEME_URI)
                )
            if subjects:
                metadata['subject'] = subjects

        catalog = dict(metadata=metadata)

        if library.opds_url:
            # TODO: Keep track of whether each library uses OPDS 1 or 2?
            cls.add_link_to_catalog(catalog, rel=cls.CATALOG_REL,
                                    href=library.opds_url,
                                    type=cls.OPDS_1_TYPE)

        if library.authentication_url:
            cls.add_link_to_catalog(catalog,
                                    href=library.authentication_url,
                                    type=AuthenticationDocument.MEDIA_TYPE)

        if library.web_url:
            cls.add_link_to_catalog(catalog, rel="alternate",
                                    href=library.web_url,
                                    type="text/html")

        if include_logo and library.logo:
            cls.add_image_to_catalog(catalog, rel=cls.THUMBNAIL_REL,
                                     href=library.logo,
                                     type="image/png")

        # Add links that allow clients to discover the library's
        # focus and eligibility area.
        for rel, route in (
            (cls.ELIGIBILITY_REL, "library_eligibility"),
            (cls.FOCUS_REL, "library_focus"),
        ):
            url = url_for(route, uuid=library.internal_urn, _external=True)
            cls.add_link_to_catalog(
                catalog, rel=rel, href=url, type="application/geo+json"
            )
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
        # Add a link for the registry's web client, if it has one.
        if web_client_uri_template:
            web_client_url = web_client_uri_template.replace('{uuid}', library.internal_urn)
            cls.add_link_to_catalog(
                catalog, href=web_client_url, rel="self", type="text/html"
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

    def __str__(self):
        if self.catalog is None:
            return None

        return json.dumps(self.catalog)
