import flask
from flask import request
from urllib.parse import unquote
from library_registry.problem_details import (
    LIBRARY_NOT_FOUND,
)
from library_registry.model import (
    Library,
)
from library_registry.opds import Annotator, OPDSCatalog
from library_registry.constants import (
    OPENSEARCH_MEDIA_TYPE,
    OPDS_CATALOG_REGISTRATION_MEDIA_TYPE,
)
from library_registry.config import Configuration

class BaseController:

    def __init__(self, app):
        self.app = app
        self._db = self.app._db

    def library_for_request(self, uuid):
        """Look up the library the user is trying to access."""
        if not uuid:
            return LIBRARY_NOT_FOUND
        if not uuid.startswith("urn:uuid:"):
            uuid = "urn:uuid:" + uuid
        library = Library.for_urn(self._db, uuid)
        if not library:
            return LIBRARY_NOT_FOUND
        request.library = library
        return library

class LibraryRegistryAnnotator(Annotator):

    def __init__(self, app):
        self.app = app

    def annotate_catalog(self, catalog, live=True):
        """Add links and metadata to every catalog."""
        if live:
            search_controller = "libr_list.search"
        else:
            search_controller = "libr_list.search_qa"
        search_url = self.app.url_for(search_controller)
        catalog.add_link_to_catalog(
            catalog.catalog, href=search_url, rel="search", type=OPENSEARCH_MEDIA_TYPE
        )
        register_url = self.app.url_for("libr.register")
        catalog.add_link_to_catalog(
            catalog.catalog, href=register_url, rel="register", type=OPDS_CATALOG_REGISTRATION_MEDIA_TYPE
        )

        # Add a templated link for getting a single library's entry.
        library_url = unquote(self.app.url_for("libr_list.library", uuid="{uuid}"))
        catalog.add_link_to_catalog(
            catalog.catalog,
            href=library_url,
            rel="http://librarysimplified.org/rel/registry/library",
            type=OPDSCatalog.OPDS_TYPE,
            templated=True
        )

        vendor_id, ignore, ignore = Configuration.vendor_id(self.app._db)
        catalog.catalog["metadata"]["adobe_vendor_id"] = vendor_id