from flask import (Response, render_template_string,
                   session, redirect, request, url_for, make_response)

from flask_jwt_extended import (create_access_token,
                                verify_jwt_in_request, get_jwt_identity, set_access_cookies, unset_jwt_cookies)

from sqlalchemy.orm import (defer, joinedload)
from library_registry.admin.templates.templates import admin as admin_template
from library_registry.util.shared_controller import BaseController
from library_registry.util.problem_detail import ProblemDetail
from library_registry.emailer import Emailer
from library_registry.model_helpers import (get_one, get_one_or_create)
from library_registry.problem_details import (
    INVALID_CONTACT_URI,
    INVALID_CREDENTIALS,
    LIBRARY_NOT_FOUND,
)
from library_registry.model import (
    Admin,
    Hyperlink,
    Library,
    ServiceArea,
    Validation,
)


class ViewController(BaseController):
    def __call__(self):
        if verify_jwt_in_request(optional=True):
            username = get_jwt_identity()
        else:
            username = session.get('username', '')
        response = Response(render_template_string(
            admin_template,
            username=username
        ))
        return response


class AdminController(BaseController):

    def __init__(self, app, emailer_class=Emailer):
        super(AdminController, self).__init__(app)
        self.emailer = emailer_class

    def log_in(self, jwt_preferred=False):
        """End point for login with requesting flask sessions or JWT tokens

        Returns:
            err: Invalid credentials
            flask session: flask session and redirect
            jwt tokens: JWT tokens as Set-Cookie headers and redirect
        """
        username = request.form.get("username")
        password = request.form.get("password")
        if not Admin.authenticate(self._db, username, password):
            return INVALID_CREDENTIALS
        if not jwt_preferred:
            session["username"] = username
            return redirect(url_for('admin.admin_view'))
        access_token = create_access_token(identity=username)
        response = make_response(
            redirect(url_for('admin.admin_view')), 302)
        set_access_cookies(response, access_token)
        return response

    def log_out(self):
        """End point for both Flask Session logout and Flask JWT Logout

        Returns:
            Response: If JWT in request will send unset jwt cookie request in headers for the browser to remove and redirect
            Response: No JWT in request will set the `session["username"] == ""` to end the flask session and redirect
        """
        if not verify_jwt_in_request(optional=True):
            session["username"] = ""
            return redirect(url_for('admin.admin_view'))
        response = make_response(redirect(url_for('admin.admin_view')))
        unset_jwt_cookies(response)
        return response

    def libraries(self, live=True):
        # Return a specific set of information about all libraries in production;
        # this generates the library list in the admin interface.
        # If :param live is set to False, libraries in testing will also be shown.
        result = []
        alphabetical = self._db.query(Library).order_by(Library.name)

        # Load all the ORM objects we'll need for these libraries in a single query.
        alphabetical = alphabetical.options(
            joinedload(Library.hyperlinks),
            joinedload('hyperlinks', 'resource'),
            joinedload('hyperlinks', 'resource', 'validation'),
            joinedload(Library.service_areas),
            joinedload('service_areas', 'place'),
            joinedload('service_areas', 'place', 'parent'),
            joinedload(Library.settings),
        )

        # Avoid transferring large fields that we won't end up using.
        alphabetical = alphabetical.options(defer('logo'))
        alphabetical = alphabetical.options(
            defer('service_areas', 'place', 'geometry'))
        alphabetical = alphabetical.options(
            defer('service_areas', 'place', 'parent', 'geometry'))

        if live:
            alphabetical = alphabetical.filter(
                Library.registry_stage == Library.PRODUCTION_STAGE)

        libraries = list(alphabetical)

        # Run a single database query to get patron counts for all
        # relevant libraries, rather than calculating this one library
        # at a time.
        patron_counts = Library.patron_counts_by_library(self._db, libraries)

        for library in alphabetical:
            uuid = library.internal_urn.split("uuid:")[1]
            patron_count = patron_counts.get(library.id, 0)
            result += [self.library_details(uuid, library, patron_count)]

        data = dict(libraries=result)
        return data

    def library_details(self, uuid, library=None, patron_count=None):
        """Return complete information about one specific library.

        :param uuid: UUID of the library in question.
        :param library: Preloaded Library object for the library in question.
        :param patron_count: Precalculated patron count for the library in question.

        :return: A dict.
        """
        if not library:
            library = self.library_for_request(uuid)

        if isinstance(library, ProblemDetail):
            return library

        # It's presumed that associated Hyperlinks and
        # ConfigurationSettings were loaded using joinedload(), as a
        # performance optimization. To avoid further database access,
        # we'll iterate over the preloaded objects and put the
        # information into Python data structures.
        hyperlink_types = [Hyperlink.INTEGRATION_CONTACT_REL,
                           Hyperlink.HELP_REL, Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL]
        hyperlinks = dict()
        for hyperlink in library.hyperlinks:
            if hyperlink.rel not in hyperlink_types:
                continue
            hyperlinks[hyperlink.rel] = hyperlink
        contact_email_hyperlink, help_email_hyperlink, copyright_email_hyperlink = [
            hyperlinks.get(rel, None) for rel in hyperlink_types
        ]
        contact_email, help_email, copyright_email = [self._get_email(
            hyperlinks.get(rel, None)) for rel in hyperlink_types]
        contact_email_validated_at, help_email_validated_at, copyright_email_validated_at = [
            self._validated_at(hyperlinks.get(rel, None)) for rel in hyperlink_types
        ]

        setting_types = [Library.PLS_ID]
        settings = dict()
        for s in library.settings:
            if s.key not in setting_types or s.external_integration is not None:
                continue
            # We use _value to access the database value directly,
            # instead of the 'value' hybrid property, which creates
            # the possibility that we'll have to go to the database to
            # try to find a default we know isn't there.
            settings[s.key] = s._value
        pls_id = settings.get(Library.PLS_ID, None)

        if patron_count is None:
            patron_count = library.number_of_patrons
        num_patrons = str(patron_count)

        basic_info = dict(
            name=library.name,
            short_name=library.short_name,
            description=library.description,
            timestamp=library.timestamp,
            internal_urn=library.internal_urn,
            online_registration=str(library.online_registration),
            pls_id=pls_id,
            number_of_patrons=num_patrons
        )
        urls_and_contact = dict(
            contact_email=contact_email,
            contact_validated=contact_email_validated_at,
            help_email=help_email,
            help_validated=help_email_validated_at,
            copyright_email=copyright_email,
            copyright_validated=copyright_email_validated_at,
            authentication_url=library.authentication_url,
            opds_url=library.opds_url,
            web_url=library.web_url,
        )

        # This will be slow unless ServiceArea has been preloaded with a joinedload().
        areas = self._areas(library.service_areas)

        stages = dict(
            library_stage=library._library_stage,
            registry_stage=library.registry_stage,
        )
        return dict(uuid=uuid, basic_info=basic_info, urls_and_contact=urls_and_contact, areas=areas, stages=stages)

    def _areas(self, areas):
        result = {}
        for (a, b) in [(ServiceArea.FOCUS, "focus"), (ServiceArea.ELIGIBILITY, "service")]:
            filtered = [place for place in areas if (place.type == a)]
            result[b] = [self._format_place_name(
                item.place) for item in filtered]
        return result

    def _format_place_name(self, place):
        return place.human_friendly_name or 'Everywhere'

    def _get_email(self, hyperlink):
        if hyperlink and hyperlink.resource and hyperlink.resource.href:
            return hyperlink.resource.href.split("mailto:")[1]

    def _validated_at(self, hyperlink):
        validated_at = "Not validated"
        if hyperlink and hyperlink.resource:
            validation = hyperlink.resource.validation
            if validation:
                return validation.started_at
        return validated_at

    def search_details(self):
        name = request.form.get("name")
        search_results = Library.search(self._db, {}, name, production=False)
        if search_results:
            info = [self.library_details(lib.internal_urn.split("uuid:")[
                                         1], lib) for lib in search_results]
            return dict(libraries=info)
        else:
            return LIBRARY_NOT_FOUND

    def validate_email(self):
        # Manually validate an email address, without the admin having to click on a confirmation link
        uuid = request.form.get("uuid")
        email = request.form.get("email")
        library = self.library_for_request(uuid)
        if isinstance(library, ProblemDetail):
            return library
        email_types = {
            "contact_email": Hyperlink.INTEGRATION_CONTACT_REL,
            "help_email": Hyperlink.HELP_REL,
            "copyright_email": Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL
        }
        hyperlink = None
        if email_types.get(email):
            hyperlink = Library.get_hyperlink(library, email_types[email])
        if not hyperlink or not hyperlink.resource or isinstance(hyperlink, ProblemDetail):
            return INVALID_CONTACT_URI.detailed(
                "The contact URI for this library is missing or invalid"
            )
        validation, is_new = get_one_or_create(
            self._db, Validation, resource=hyperlink.resource)
        validation.restart()
        validation.mark_as_successful()

        return self.library_details(uuid)

    def edit_registration(self):
        # Edit a specific library's registry_stage and library_stage based on
        # information which an admin has submitted in the interface.
        uuid = request.form.get("uuid")
        library = self.library_for_request(uuid)
        if isinstance(library, ProblemDetail):
            return library
        registry_stage = request.form.get("Registry Stage")
        library_stage = request.form.get("Library Stage")

        library._library_stage = library_stage
        library.registry_stage = registry_stage
        return Response(str(library.internal_urn), 200)

    def add_or_edit_pls_id(self):
        uuid = request.form.get("uuid")
        library = self.library_for_request(uuid)
        if isinstance(library, ProblemDetail):
            return library
        pls_id = request.form.get(Library.PLS_ID)
        library.pls_id.value = pls_id
        return Response(str(library.internal_urn), 200)


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

        if (not validation or not validation.resource or validation.resource.id != resource_id):
            # For whatever reason the resource ID and secret don't match.
            # A generic error that doesn't reveal information is appropriate
            # in all cases.
            error = _("Confirmation code %r not found") % secret
            return self.html_response(404, error)

        # At this point we know that the resource has not been
        # confirmed, and that the secret matches the resource. The
        # only other problem might be that the validation has expired.
        if not validation.active:
            error = _(
                "Confirmation code %r has expired. Re-register to get another code.") % secret
            return self.html_response(400, error)
        validation.mark_as_successful()

        resource = validation.resource
        message = ("You successfully confirmed %s.") % resource.href
        return self.html_response(200, message)
