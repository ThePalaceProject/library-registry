import base64
import feedparser
from flask_babel import lazy_gettext as _
import json
import logging
from nose.tools import set_trace
from PIL import Image
from StringIO import StringIO
from urlparse import urljoin

from authentication_document import AuthenticationDocument
from opds import OPDSCatalog
from model import (
    get_one,
    get_one_or_create,
    Hyperlink,
    Library,
)
from problem_details import *
from util.http import (
    HTTP,
    RequestTimedOut,
)
from util.problem_detail import ProblemDetail


class LibraryRegistrar(object):
    """Encapsulates the logic of the library registration process."""

    def __init__(self, _db, do_get=HTTP.debuggable_get):
        self._db = _db
        self.do_get = do_get
        self.log = logging.getLogger("Library registrar")

    @classmethod
    def reregister(cls, library):
        """Re-register the given Library by fetching its authentication
        document and updating its record appropriately.

        This process will not be as thorough as one initiated manually
        by the library administrator, but it can be used to
        automatically keep us up to date on minor changes to a
        library's description, logo, etc.
        
        :param library: A Library.

        :return: A ProblemDetail if there's a problem. Otherwise, None.
        """
        _db = Session.object_session(library)

        # We don't provide the shared secret to avoid complications
        # when someone else now controls the
        # library.authentication_url. In general, we trust what the
        # authentication document says, but we won't believe the
        # circulation manager moved unless the registration process
        # was manually initiated by a library administrator who knows
        # the secret.
        result = cls.register(
            auth_url = library.authentication_url, shared_secret=None,
            library_stage=library.library_stage,
        )
        if isinstance(result, ProblemDetail):
            return result

        # The return value may include new settings for contact
        # hyperlinks, but we will not be changing any Hyperlink
        # objects, since that might result in emails being sent out
        # unexpectedly. The library admin must explicitly re-register
        # for that to happen.
        #
        # Basically, we don't actually use any of the items returned
        # by register() -- only the controller uses that stuff.
        return None

    def register(self, auth_url, shared_secret, library_stage):
        """Register the given authentication document as a library in this
        registry, if possible.

        :param auth_url: The URL to the library's authentication document.

        :param shared_secret: A preexisting shared secret between the
            library and the registry. This is optional, but providing it will
            allow you to do things you wouldn't normally be able to
            do, such as change the library's circulation manager URL.

        :param library_stage: The library administrator's proposed value for
            Library.library_stage.

        :return: A ProblemDetail if there's a problem. Otherwise, a 5-tuple
            (library, is_new, from_shared_secret, auth_document, new_hyperlinks,
             elevated_permissions).

        `from_shared_secret` is True if `shared_secret` was
             and actually useful when looking up `library`.
        `auth_document` is an AuthenticationDocument corresponding to
            the library's authentication document, as found at auth_url.
        `new_hyperlinks` is a list of Hyperlinks
             that ought to be created for registration to be complete.

        """
        hyperlinks_to_create = []

        auth_response = self._make_request(
            auth_url,
            auth_url,
            _("No Authentication For OPDS document present at %(url)s",
              url=auth_url),
            _("Timeout retrieving auth document %(url)s", url=auth_url),
            _("Error retrieving auth document %(url)s", url=auth_url),
        )
        if isinstance(auth_response, ProblemDetail):
            return auth_response
        try:
            auth_document = AuthenticationDocument.from_string(self._db, auth_response.content)
        except Exception, e:
            self.log.error(
                "Registration of %s failed: invalid auth document.",
                auth_url, exc_info=e
            )
            return INVALID_INTEGRATION_DOCUMENT
        failure_detail = None
        if not auth_document.id:
            failure_detail = _("The OPDS authentication document is missing an id.")
        if not auth_document.title:
            failure_detail = _("The OPDS authentication document is missing a title.")
        if auth_document.root:
            opds_url = auth_document.root['href']
        else:
            failure_detail = _("The OPDS authentication document is missing a 'start' link to the root OPDS feed.")

        if auth_document.id != auth_response.url:
            failure_detail = _("The OPDS authentication document's id (%(id)s) doesn't match its url (%(url)s).", id=auth_document.id, url=auth_response.url)
        if failure_detail:
            self.log.error(
                "Registration of %s failed: %s", auth_url, failure_detail
            )
            return INVALID_INTEGRATION_DOCUMENT.detailed(failure_detail)

        # Make sure the authentication document includes a way for
        # patrons to get help or file a copyright complaint. These
        # links must be stored in the database as Hyperlink objects.
        links = auth_document.links or []
        for rel, problem_title in [
            ('help', "Invalid or missing patron support email address"),
            (Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL,
             "Invalid or missing copyright designated agent email address")
        ]:
            uris = self._locate_email_addresses(rel, links, problem_title)
            if isinstance(uris, ProblemDetail):
                return uris
            hyperlinks_to_create.append((rel, uris))

        # Cross-check the opds_url to make sure it links back to the
        # authentication document.
        opds_response = self._make_request(
            auth_url,
            opds_url,
            _("No OPDS root document present at %(url)s", url=opds_url),
            _("Timeout retrieving OPDS root document at %(url)s", url=opds_url),
            _("Error retrieving OPDS root document at %(url)s", url=opds_url),
            allow_401 = True
        )
        if isinstance(opds_response, ProblemDetail):
            return opds_response

        content_type = opds_response.headers.get('Content-Type')
        failure_detail = None
        if opds_response.status_code == 401:
            # This is only acceptable if the server returned a copy of
            # the Authentication For OPDS document we just got.
            if content_type != AuthenticationDocument.MEDIA_TYPE:
                failure_detail = _("401 response at %(url)s did not yield an Authentication For OPDS document", url=opds_url)
            elif not self.opds_response_links_to_auth_document(
                    opds_response, auth_url
            ):
                failure_detail = _("Authentication For OPDS document guarding %(opds_url)s does not match the one at %(auth_url)s", opds_url=opds_url, auth_url=auth_url)
        elif content_type not in (OPDSCatalog.OPDS_TYPE,
                                OPDSCatalog.OPDS_1_TYPE):
            failure_detail = _("Supposed root document at %(url)s is not an OPDS document", url=opds_url)
        elif not self.opds_response_links_to_auth_document(
                opds_response, auth_url
        ):
            failure_detail = _("OPDS root document at %(opds_url)s does not link back to authentication document %(auth_url)s", opds_url=opds_url, auth_url=auth_url)

        if failure_detail:
            self.log.error(
                "Registration of %s failed: %s", auth_url, failure_detail
            )
            return INVALID_INTEGRATION_DOCUMENT.detailed(failure_detail)

        library = None
        elevated_permissions = False
        auth_url = auth_response.url
        if shared_secret:
            # Look up a library by the provided shared secret. This
            # will let us handle the case where the library has
            # changed URLs (auth_url does not match
            # library.authentication_url) but the shared secret is the
            # same.
            library = get_one(
                self._db, Library,
                shared_secret=shared_secret
            )
            # This gives the requestor an elevated level of permissions.
            elevated_permissions = True
            is_new = False

        if not library:
            # Either this is a library at a known authentication URL
            # or it's a brand new library.
            library, is_new = get_one_or_create(
                self._db, Library,
                authentication_url=auth_url
            )
            if opds_url:
                library.opds_url = opds_url

        if elevated_permissions and (
            library.authentication_url != auth_url
            or library.opds_url != opds_url
        ):
            # The library's authentication URL and/or OPDS URL has
            # changed, e.g. moved from HTTP to HTTPS. The registration
            # includes a valid shared secret, so it's okay to modify
            # the corresponding database fields.
            result = self._update_library_authentication_url(
                library, auth_url, opds_url, shared_secret
            )
            if isinstance(result, ProblemDetail):
                return result

        try:
            library.library_stage = library_stage
        except ValueError, e:
            return LIBRARY_ALREADY_IN_PRODUCTION
        library.name = auth_document.title
        if auth_document.website:
            url = auth_document.website.get("href")
            if url:
                url = urljoin(opds_url, url)
            library.web_url = auth_document.website.get("href")
        else:
            library.web_url = None

        if auth_document.logo:
            library.logo = auth_document.logo
        elif auth_document.logo_link:
            url = auth_document.logo_link.get("href")
            if url:
                url = urljoin(opds_url, url)
            logo_response = self.do_get(url, stream=True)
            try:
                image = Image.open(logo_response.raw)
            except Exception, e:
                image_url = auth_document.logo_link.get("href")
                self.log.error(
                    "Registration of %s failed: could not read logo image %s",
                    auth_url, image_url
                )
                return INVALID_INTEGRATION_DOCUMENT.detailed(
                    _("Could not read logo image %(image_url)s", image_url=image_url)
                )
            # Convert to PNG.
            buffer = StringIO()
            image.save(buffer, format="PNG")
            b64 = base64.b64encode(buffer.getvalue())
            type = logo_response.headers.get("Content-Type") or auth_document.logo_link.get("type")
            if type:
                library.logo = "data:%s;base64,%s" % (type, b64)
        else:
            library.logo = None
        problem = auth_document.update_library(library)
        if problem:
            self.log.error(
                "Registration of %s failed: problem during registration: %r",
                auth_url, problem
            )
            return problem

        return (library, is_new, elevated_permissions, auth_document,
                hyperlinks_to_create)


    def _make_request(self, registration_url, url, on_404, on_timeout, on_exception, allow_401=False):
        allowed_codes = ["2xx", "3xx", 404]
        if allow_401:
            allowed_codes.append(401)
        try:
            response = self.do_get(
                url, allowed_response_codes=allowed_codes,
                timeout=30
            )
            # We only allowed 404 above so that we could return a more
            # specific problem detail document if it happened.
            if response.status_code == 404:
                return INTEGRATION_DOCUMENT_NOT_FOUND.detailed(on_404)
            if not allow_401 and response.status_code == 401:
                self.log.error(
                    "Registration of %s failed: %s is behind authentication gateway",
                    auth_url, url
                )
                return ERROR_RETRIEVING_DOCUMENT.detailed(
                    _("%(url)s is behind an authentication gateway",
                      url=url)
                )
        except RequestTimedOut, e:
            self.log.error(
                "Registration of %s failed: timeout retrieving %s",
                registration_url, url, exc_info=e
            )
            return TIMEOUT.detailed(on_timeout)
        except Exception, e:
            self.log.error(
                "Registration of %s failed: error retrieving %s",
                registration_url, url, exc_info=e
            )
            return ERROR_RETRIEVING_DOCUMENT.detailed(on_exception)
        return response

    @classmethod
    def opds_response_links(cls, response, rel):
        """Find all the links in the given response for the given
        link relation.
        """
        # Look in the response itself for a Link header.
        links = []
        link = response.links.get(rel)
        if link:
            links.append(link.get('url'))
        media_type = response.headers.get('Content-Type')
        if media_type == OPDSCatalog.OPDS_TYPE:
            # Parse as OPDS 2.
            catalog = json.loads(response.content)
            links = []
            for k,v in catalog.get("links", {}).iteritems():
                if k == rel:
                    links.append(v.get("href"))
        elif media_type == OPDSCatalog.OPDS_1_TYPE:
            # Parse as OPDS 1.
            feed = feedparser.parse(response.content)
            for link in feed.get("feed", {}).get("links", []):
                if link.get('rel') == rel:
                    links.append(link.get('href'))
        elif media_type == AuthenticationDocument.MEDIA_TYPE:
            document = json.loads(response.content)
            if isinstance(document, dict):
                links.append(document.get('id'))
        return [urljoin(response.url, url) for url in links if url]

    @classmethod
    def opds_response_links_to_auth_document(cls, opds_response, auth_url):
        """Verify that the given response links to the given URL as its
        Authentication For OPDS document.

        The link might happen in the `Link` header or in the body of
        an OPDS feed.
        """
        links = []
        try:
            links = cls.opds_response_links(
                opds_response, AuthenticationDocument.AUTHENTICATION_DOCUMENT_REL
            )
        except ValueError, e:
            # The response itself is malformed.
            return False
        return auth_url in links

    @classmethod
    def _locate_email_addresses(cls, rel, links, problem_title):
        """Find one or more email addresses in a list of links, all with
        a given `rel`.

        :param library: A Library
        :param rel: The rel for this type of link.
        :param links: A list of dictionaries with keys 'rel' and 'href'
        :problem_title: The title to use in a ProblemDetail if no
            valid links are found.
        :return: Either a list of candidate links or a customized ProblemDetail.
        """
        candidates = []
        for link in links:
            if link.get('rel') != rel:
                # Wrong kind of link.
                continue
            uri = link.get('href')
            value = cls._required_email_address(uri, problem_title)
            if isinstance(value, basestring):
                candidates.append(value)

        # There were no relevant links.
        if not candidates:
            problem = INVALID_CONTACT_URI.detailed(
                "No valid mailto: links found with rel=%s" % rel
            )
            problem.title = problem_title
            return problem

        return candidates

    @classmethod
    def _required_email_address(cls, uri, problem_title):
        """Verify that `uri` is a mailto: URI.

        :return: Either a mailto: URI or a customized ProblemDetail.
        """
        problem = None
        on_error = INVALID_CONTACT_URI
        if not uri:
            problem = on_error.detailed("No email address was provided")
        elif not uri.startswith("mailto:"):
            problem = on_error.detailed(
                _("URI must start with 'mailto:' (got: %s)") % uri
            )
        if problem:
            problem.title = problem_title
            return problem
        return uri

    @classmethod
    def _update_library_authentication_url(
            cls, library, new_authentication_url,
            new_opds_url, provided_shared_secret,
    ):
        """Change a library's authentication URL, assuming the provided shared
        secret gives the requester that permission.

        :param library: A Library
        :param new_authentication_url: A proposed new value for
            Library.authentication_url
        :param new_opds_url: A proposed new value for Library.opds_url.
        :param provided_shared_secret: Allegedly, the library's
            shared secret.
        """
        if library.shared_secret != provided_shared_secret:
            return AUTHENTICATION_FAILURE.detailed(
                _("Provided shared secret is invalid")
            )
        if new_authentication_url:
            library.authentication_url = new_authentication_url
        if new_opds_url:
            library.opds_url = new_opds_url
        return None
