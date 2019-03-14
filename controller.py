from nose.tools import set_trace
import logging
import flask
from flask_babel import lazy_gettext as _
from flask import (
    Response,
    redirect,
    url_for,
    session,
)
import requests
from smtplib import SMTPException
import json
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import base64
import os
from urllib import unquote

from adobe_vendor_id import AdobeVendorIDController
from authentication_document import AuthenticationDocument
from emailer import Emailer
from model import (
    ConfigurationSetting,
    Hyperlink,
    Library,
    Place,
    Resource,
    ServiceArea,
    Validation,
    get_one,
    get_one_or_create,
    production_session,
)
from config import (
    Configuration,
    CannotLoadConfiguration,
)
from opds import (
    Annotator,
    OPDSCatalog,
)
from registrar import LibraryRegistrar
from templates import admin as admin_template
from util import GeometryUtility
from util.app_server import (
    HeartbeatController,
    catalog_response,
)
from util.http import (
    HTTP,
)
from util.problem_detail import ProblemDetail
from problem_details import *

OPENSEARCH_MEDIA_TYPE = "application/opensearchdescription+xml"
OPDS_CATALOG_REGISTRATION_MEDIA_TYPE = "application/opds+json;profile=https://librarysimplified.org/rel/profile/directory"

class LibraryRegistry(object):

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
        self.registry_controller = LibraryRegistryController(
            self, emailer_class
        )
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


class LibraryRegistryAnnotator(Annotator):

    def __init__(self, app):
        self.app = app

    def annotate_catalog(self, catalog, live=True):
        """Add links and metadata to every catalog."""
        if live:
            search_controller = "search"
        else:
            search_controller = "search_qa"
        search_url = self.app.url_for(search_controller)
        catalog.add_link_to_catalog(
            catalog.catalog, href=search_url, rel="search", type=OPENSEARCH_MEDIA_TYPE
        )
        register_url = self.app.url_for("register")
        catalog.add_link_to_catalog(
            catalog.catalog, href=register_url, rel="register", type=OPDS_CATALOG_REGISTRATION_MEDIA_TYPE
        )

        # Add a templated link for getting a single library's entry.
        library_url = unquote(self.app.url_for("library", uuid="{uuid}"))
        catalog.add_link_to_catalog(
            catalog.catalog, href=library_url, rel="http://librarysimplified.org/rel/registry/library", type=OPDSCatalog.OPDS_TYPE, templated=True)

        vendor_id, ignore, ignore = Configuration.vendor_id(self.app._db)
        catalog.catalog["metadata"]["adobe_vendor_id"] = vendor_id

class BaseController(object):

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
        flask.request.library = library
        return library

class StaticFileController(BaseController):
    def static_file(self, directory, filename):
        return flask.send_from_directory(directory, filename, cache_timeout=None)


class ViewController(BaseController):
    def __call__(self):
        username = session.get('username', '')
        response = Response(flask.render_template_string(
            admin_template,
            username=username
        ))
        return response

class LibraryRegistryController(BaseController):

    OPENSEARCH_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
 <OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
   <ShortName>%(name)s</ShortName>
   <Description>%(description)s</Description>
   <Tags>%(tags)s</Tags>
   <Url type="application/atom+xml;profile=opds-catalog" template="%(url_template)s"/>
 </OpenSearchDescription>"""

    def __init__(self, app, emailer_class=Emailer):
        super(LibraryRegistryController, self).__init__(app)
        self.annotator = LibraryRegistryAnnotator(app)
        self.log = self.app.log
        emailer = None
        try:
            emailer = emailer_class.from_sitewide_integration(self._db)
        except CannotLoadConfiguration, e:
            self.log.error(
                "Cannot load email configuration. Will not be sending any emails.",
                exc_info=e
            )
        self.emailer = emailer

    def nearby(self, location, live=True):
        qu = Library.nearby(self._db, location, production=live)
        qu = qu.limit(5)
        if live:
            nearby_controller = 'nearby'
        else:
            nearby_controller = 'nearby_qa'
        this_url = self.app.url_for(nearby_controller)
        catalog = OPDSCatalog(
            self._db, unicode(_("Libraries near you")), this_url, qu,
            annotator=self.annotator, live=live
        )
        return catalog_response(catalog)

    def search(self, location, live=True):
        query = flask.request.args.get('q')
        if live:
            search_controller = 'search'
        else:
            search_controller = 'search_qa'
        if query:
            # Run the query and send the results.
            results = Library.search(
                self._db, location, query, production=live
            )

            this_url = self.app.url_for(
                search_controller, q=query
            )
            catalog = OPDSCatalog(
                self._db, unicode(_('Search results for "%s"')) % query,
                this_url, results,
                annotator=self.annotator, live=live
            )
            return catalog_response(catalog)
        else:
            # Send the search form.
            body = self.OPENSEARCH_TEMPLATE % dict(
                name=_("Find your library"),
                description=_("Search by ZIP code, city or library name."),
                tags="",
                url_template = self.app.url_for(search_controller) + "?q={searchTerms}"
            )
            headers = {}
            headers['Content-Type'] = OPENSEARCH_MEDIA_TYPE
            headers['Cache-Control'] = "public, no-transform, max-age: %d" % (
                3600 * 24 * 30
            )
            return Response(body, 200, headers)

    def libraries(self):
        # Return a specific set of information about all libraries; this generates the library list in the admin interface,
        libraries = []
        all = self._db.query(Library).order_by(Library.name)
        for library in all:
            uuid = library.internal_urn.split("uuid:")[1]
            libraries += [self.library_details(uuid, library)]
        return dict(libraries=libraries)

    def library_details(self, uuid, library=None):
        # Return complete information about one specific library.
        if not library:
            library = self.library_for_request(uuid)

        if isinstance(library, ProblemDetail):
            return library

        contact_email = None

        hyperlink = Library.get_hyperlink(library, Hyperlink.INTEGRATION_CONTACT_REL)
        contact_email = self._contact_email(hyperlink)
        validated_at = self._validated_at(hyperlink)

        basic_info = dict(
            name=library.name,
            short_name=library.short_name,
            description=library.description,
            timestamp=library.timestamp,
            internal_urn=library.internal_urn,
            online_registration=str(library.online_registration),
        )
        urls_and_contact = dict(
            contact_email=contact_email,
            validated=validated_at,
            authentication_url=library.authentication_url,
            opds_url=library.opds_url,
            web_url=library.web_url,
        )
        stages = dict(
            library_stage=library._library_stage,
            registry_stage=library.registry_stage,
        )
        return dict(uuid=uuid, basic_info=basic_info, urls_and_contact=urls_and_contact, stages=stages)

    def _contact_email(self, hyperlink):
        if hyperlink and hyperlink.resource and hyperlink.resource.href:
            return hyperlink.resource.href.split("mailto:")[1]

    def _validated_at(self, hyperlink):
        validated_at = "Not validated"
        if hyperlink and hyperlink.resource:
            validation = get_one(self._db, Validation, resource=hyperlink.resource)
            if validation:
                return validation.started_at
        return validated_at

    def validate_email(self):
        # Manually validate an email address, without the admin having to click on a confirmation link
        uuid = flask.request.form.get("uuid")
        library = self.library_for_request(uuid)
        hyperlink = Library.get_hyperlink(library, Hyperlink.INTEGRATION_CONTACT_REL)
        if not hyperlink or not hyperlink.resource or isinstance(hyperlink, ProblemDetail):
            return INVALID_CONTACT_URI.detailed(
                "The contact URI for this library is missing or invalid"
            )

        validation, is_new = get_one_or_create(self._db, Validation, resource=hyperlink.resource)
        validation.restart()
        validation.mark_as_successful()

        return self.library_details(uuid)

    def edit_registration(self):
        # Edit a specific library's registry_stage and library_stage based on information which an admin has submitted in the interface.
        uuid = flask.request.form.get("uuid")
        library = self.library_for_request(uuid)
        if isinstance(library, ProblemDetail):
            return library
        registry_stage = flask.request.form.get("Registry Stage")
        library_stage = flask.request.form.get("Library Stage")

        library._library_stage = library_stage
        library.registry_stage = registry_stage
        return Response(unicode(library.internal_urn), 200)

    def log_in(self):
        username = flask.request.form.get("username")
        password = flask.request.form.get("password")
        if username == "Admin" and password == "123":
            session["username"] = username
            return redirect(url_for('admin_view'))
        else:
            return INVALID_CREDENTIALS

    def log_out(self):
        session["username"] = "";
        return redirect(url_for('admin_view'))

    def library(self):
        library = flask.request.library
        this_url = self.app.url_for(
            'library', uuid=library.internal_urn
        )
        catalog = OPDSCatalog(
            self._db, library.name,
            this_url, [library],
            annotator=self.annotator, live=False,
        )
        return catalog_response(catalog)

    def render(self):
        response = Response(flask.render_template_string(
            admin_template
        ))
        return response

    @property
    def registration_document(self):
        """Serve a document that describes the registration process,
        notably the terms of service for that process.

        The terms of service are hosted elsewhere; we only know the
        URL of the page they're stored.
        """
        document = dict()
        terms_of_service_url = ConfigurationSetting.sitewide(
            self._db, Configuration.REGISTRATION_TERMS_OF_SERVICE_URL
        ).value
        if terms_of_service_url:
            OPDSCatalog.add_link_to_catalog(
                document, rel="terms-of-service",
                href=terms_of_service_url
            )
        return document

    def catalog_response(self, document, status=200):
        """Serve an OPDS 2.0 catalog."""
        if not isinstance(document, basestring):
            document = json.dumps(document)
        headers = { "Content-Type": OPDS_CATALOG_REGISTRATION_MEDIA_TYPE }
        return Response(document, status, headers=headers)

    def register(self, do_get=HTTP.debuggable_get):
        if flask.request.method == 'GET':
            document = self.registration_document
            return self.catalog_response(document)

        auth_url = flask.request.form.get("url")
        self.log.info("Got request to register %s", auth_url)
        if not auth_url:
            return NO_AUTH_URL

        integration_contact_uri = flask.request.form.get("contact")
        integration_contact_email = integration_contact_uri
        shared_secret = None
        auth_header = flask.request.headers.get('Authorization')
        if auth_header and isinstance(auth_header, basestring) and "bearer" in auth_header.lower():
            shared_secret = auth_header.split(' ', 1)[1]
            self.log.info("Incoming shared secret: %s...", shared_secret[:4])

        # If 'stage' is not provided, it means the client doesn't make the
        # testing/production distinction. We have to assume they want
        # production -- otherwise they wouldn't bother registering.

        library_stage = flask.request.form.get("stage")
        self.log.info("Incoming stage: %s", library_stage)
        library_stage = library_stage or Library.PRODUCTION_STAGE


        # NOTE: This is commented out until we can say that
        # registration requires providing a contact email and expect
        # every new library to be on a circulation manager that can meet
        # this requirement.
        #
        #integration_contact_email = self._required_email_address(
        #    integration_contact_uri,
        #    "Invalid or missing configuration contact email address"
        #)
        if isinstance(integration_contact_email, ProblemDetail):
            return integration_contact_email

        # Registration is a complex multi-step process. Start a subtransaction
        # so we can back out of the whole thing if any part of it fails.
        __transaction = self._db.begin_nested()

        library = None
        elevated_permissions = False
        if shared_secret:
            # Look up a library by the provided shared secret. This
            # will let us handle the case where the library has
            # changed URLs (auth_url does not match
            # library.authentication_url) but the shared secret is the
            # same.
            library = get_one(self._db, Library, shared_secret=shared_secret)
            if not library:
                return AUTHENTICATION_FAILURE.detailed(
                    _("Provided shared secret is invalid")
                )

            # This gives the requestor an elevated level of permissions.
            elevated_permissions = True
            library_is_new = False

            if library.authentication_url != auth_url:
                # The library's authentication URL has changed,
                # e.g. moved from HTTP to HTTPS. The registration
                # includes a valid shared secret, so it's okay to
                # modify the corresponding database field.
                #
                # We want to do this before the registration, so that
                # we request the new URL instead of the old one.
                library.authentication_url = auth_url

        if not library:
            # Either this is a library at a known authentication URL
            # or it's a brand new library.
            library, library_is_new = get_one_or_create(
                self._db, Library,
                authentication_url=auth_url
            )

        registrar = LibraryRegistrar(self._db, do_get=do_get)
        result = registrar.register(library, library_stage)
        if isinstance(result, ProblemDetail):
            __transaction.rollback()
            return result

        # At this point registration (or re-registration) has
        # succeeded, so we won't be rolling back the subtransaction
        # that created the Library.
        __transaction.commit()
        auth_document, hyperlinks_to_create = result

        # Now that we've completed the registration process, we
        # know the opds_url -- it's the 'start' link found in
        # the auth_document.
        #
        # Registration will fail if this link is missing or the
        # URL doesn't work, so we can assume this is valid.
        opds_url = auth_document.root['href']

        if library_is_new:
            # The library was just created, so it had no opds_url.
            # Set it now.
            library.opds_url = opds_url

        # The registration process may have queued up a number of
        # Hyperlinks that needed to be created (taken from the
        # library's authentication document), but we also need to
        # create a hyperlink for the integration contact provided with
        # the registration request itself.
        if integration_contact_email:
            hyperlinks_to_create.append(
                (Hyperlink.INTEGRATION_CONTACT_REL, [integration_contact_email])
            )

        reset_shared_secret = False
        if elevated_permissions:
            # If you have elevated permissions you may ask for the
            # shared secret to be reset.
            reset_shared_secret = flask.request.form.get(
                "reset_shared_secret", False
            )

            if library.opds_url != opds_url:
                # The library's OPDS URL has changed, e.g. moved from
                # HTTP to HTTPS. Since we have elevated permissions,
                # it's okay to modify the corresponding database
                # field.
                library.opds_url = opds_url

        for rel, candidates in hyperlinks_to_create:
            hyperlink, is_modified = library.set_hyperlink(rel, *candidates)
            if is_modified:
                # We need to send an email to this email address about
                # what just happened. This is either so the receipient
                # can confirm that the address works, or to inform
                # them a new library is using their address.
                try:
                    hyperlink.notify(self.emailer, self.app.url_for)
                except SMTPException, e:
                    # We were unable to send the email.
                    return INTEGRATION_ERROR.detailed(
                        _("SMTP error while sending email to %(address)s",
                          address=hyperlink.resource.href)
                    )

        # Create an OPDS 2 catalog containing all available
        # information about the library.
        catalog = OPDSCatalog.library_catalog(
            library, include_private_information=True,
            url_for=self.app.url_for
        )

        # Annotate the catalog with some information specific to
        # the transaction that's happening right now.
        public_key = auth_document.public_key
        if public_key and public_key.get("type") == "RSA":
            public_key = RSA.importKey(public_key.get("value"))
            encryptor = PKCS1_OAEP.new(public_key)

            if not library.short_name:
                def dupe_check(candidate):
                    return Library.for_short_name(self._db, candidate) is not None
                library.short_name = Library.random_short_name(dupe_check)

            generate_secret = (
                (library.shared_secret is None) or reset_shared_secret
            )
            if generate_secret:
                library.shared_secret = os.urandom(24).encode('hex')

            encrypted_secret = encryptor.encrypt(str(library.shared_secret))

            catalog["metadata"]["short_name"] = library.short_name
            catalog["metadata"]["shared_secret"] = base64.b64encode(encrypted_secret)

        if library_is_new:
            status_code = 201
        else:
            status_code = 200
        return self.catalog_response(catalog, status_code)


class ValidationController(BaseController):
    """Validates Resources based on validation codes.

    The confirmation codes were sent out in emails to the addresses that
    need to be validated, or otherwise communicated to someone who needs
    to click on the link to this controller.
    """

    MESSAGE_TEMPLATE = "<html><head><title>%(message)s</title><body>%(message)s</body></html>"

    def html_response(self, status_code, message):
        """Return a human-readable message as a minimal HTML page.

        This controller is used by human beings, so HTML is better
        than Problem Detail Documents.
        """
        headers = {"Content-Type": "text/html"}
        page = self.MESSAGE_TEMPLATE % dict(message=message)
        return Response(page, status_code, headers=headers)

    def confirm(self, resource_id, secret):
        """Confirm a secret for a URI, or don't.

        :return: A Response containing a simple HTML document.
        """
        if not secret:
            return self.html_response(404, _("No confirmation code provided"))
        if not resource_id:
            return self.html_response(404, _("No resource ID provided"))
        validation = get_one(self._db, Validation, secret=secret)
        resource = get_one(self._db, Resource, id=resource_id)
        if not resource:
            return self.html_response(404, _("No such resource"))

        if not validation:
            # The secret is invalid. This might be because the secret
            # is wrong, or because the Resource has already been
            # validated.
            #
            # Let's eliminate the 'Resource has already been validated'
            # possibility and take care of the other case next.
            if resource and resource.validation and resource.validation.success:
                return self.html_response(200, _("This URI has already been validated."))

        if (not validation
            or not validation.resource
            or validation.resource.id != resource_id):
            # For whatever reason the resource ID and secret don't match.
            # A generic error that doesn't reveal information is appropriate
            # in all cases.
            error = _("Confirmation code %r not found") % secret
            return self.html_response(404, error)

        # At this point we know that the resource has not been
        # confirmed, and that the secret matches the resource. The
        # only other problem might be that the validation has expired.
        if not validation.active:
            error = _("Confirmation code %r has expired. Re-register to get another code.") % secret
            return self.html_response(400, error)
        validation.mark_as_successful()

        resource = validation.resource
        message = _("You successfully confirmed %s.") % resource.href
        return self.html_response(200, message)


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
        coverage = flask.request.args.get('coverage')
        try:
            coverage = json.loads(coverage)
        except ValueError, e:
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
        areas = [x.place for x in flask.request.library.service_areas
                 if x.type==service_type]
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
