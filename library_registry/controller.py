import json
import logging

import flask
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
from flask import (Response, request, url_for)
from flask_babel import lazy_gettext as _

from library_registry.drm.controller import AdobeVendorIDController
from library_registry.authentication_document import AuthenticationDocument
from library_registry.emailer import Emailer
from library_registry.model import (
    Place,
    ServiceArea,
    production_session,
)
from library_registry.admin.controller import ViewController
from library_registry.admin.controller import AdminController
from library_registry.library_registration_protocol.controller import LibraryRegistryController, ValidationController
from library_registry.library_list.controller import LibraryListController
from library_registry.util.shared_controller import BaseController
from library_registry.config import Configuration
from library_registry.util.app_server import HeartbeatController

class LibraryRegistry:

    def __init__(self, _db=None, testing=False, emailer_class=Emailer):

        self.log = logging.getLogger("Library registry web app")

        if _db is None and not testing:
            _db = production_session()
        self._db = _db

        self.testing = testing

        self.setup_controllers(emailer_class)

    def setup_controllers(self, emailer_class=Emailer):
        """Set up all the controllers that will be used by the web app."""
        self.view_controller = ViewController(self)
        self.admin_controller = AdminController(self)
        self.registry_controller = LibraryRegistryController(
            self, emailer_class
        )
        self.list_controller = LibraryListController(self, emailer_class)
        self.validation_controller = ValidationController(self)
        self.coverage_controller = CoverageController(self)
        self.static_files = StaticFileController(self)
        self.heartbeat = HeartbeatController()
        vendor_id, node_value, delegates = Configuration.vendor_id(self._db)
        if vendor_id:
            self.adobe_vendor_id = AdobeVendorIDController(
                self._db, vendor_id, node_value, delegates
            )
        else:
            self.adobe_vendor_id = None

    def url_for(self, view, *args, **kwargs):
        kwargs['_external'] = True
        return url_for(view, *args, **kwargs)

# This static_file function is used only when the app is running locally *without* Docker.
# In all other cases, nginx serves the static files (see docker/nginx.conf).
class StaticFileController(BaseController):
    def static_file(self, directory, filename):
        return flask.send_from_directory(directory, filename, cache_timeout=None)

class CoverageController(BaseController):
    """Converts coverage area descriptions to GeoJSON documents
    so they can be visualized.
    """

    def geojson_response(self, document):
        if isinstance(document, dict):
            document = json.dumps(document)
        headers = {"Content-Type": "application/geo+json"}
        return Response(document, 200, headers=headers)

    def lookup(self):
        coverage = request.args.get('coverage')
        try:
            coverage = json.loads(coverage)
        except ValueError:
            pass
        places, unknown, ambiguous = AuthenticationDocument.parse_coverage(
            self._db, coverage
        )
        document = Place.to_geojson(self._db, *places)

        # Extend the GeoJSON with extra information about parts of the
        # coverage document we found ambiguous or couldn't associate
        # with a Place.
        if unknown:
            document['unknown'] = unknown
        if ambiguous:
            document['ambiguous'] = ambiguous
        return self.geojson_response(document)

    def _geojson_for_service_area(self, service_type):
        """Serve a GeoJSON document describing some subset of the active
        library's service areas.
        """
        areas = [
            x.place for x in request.library.service_areas if x.type == service_type]
        return self.geojson_response(Place.to_geojson(self._db, *areas))

    def eligibility_for_library(self):
        """Serve a GeoJSON document representing the eligibility area
        for a specific library.
        """
        return self._geojson_for_service_area(ServiceArea.ELIGIBILITY)

    def focus_for_library(self):
        """Serve a GeoJSON document representing the focus area
        for a specific library.
        """
        return self._geojson_for_service_area(ServiceArea.FOCUS)