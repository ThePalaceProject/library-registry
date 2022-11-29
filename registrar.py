from __future__ import annotations

import base64
import json
import logging
import re
from io import BytesIO
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import feedparser
from flask_babel import lazy_gettext as _
from PIL import Image

from authentication_document import AuthenticationDocument
from model import Hyperlink
from opds import OPDSCatalog
from problem_details import (
    ERROR_RETRIEVING_DOCUMENT,
    INTEGRATION_DOCUMENT_NOT_FOUND,
    INVALID_CONTACT_URI,
    INVALID_INTEGRATION_DOCUMENT,
    LIBRARY_ALREADY_IN_PRODUCTION,
    TIMEOUT,
)
from util.file_storage import LibraryLogoStore
from util.http import HTTP, RequestTimedOut
from util.problem_detail import ProblemDetail

if TYPE_CHECKING:
    from model import Library


class VerifyLinkRegexes:
    MAILTO = r"^mailto:"
    HTTP_OR_MAILTO = r"^(http[s]?:|mailto:)"


class LibraryRegistrar(object):
    """Encapsulates the logic of the library registration process."""

    def __init__(self, _db, do_get=HTTP.debuggable_get):
        self._db = _db
        self.do_get = do_get
        self.log = logging.getLogger("Library registrar")

    def reregister(self, library):
        """Re-register the given Library by fetching its authentication
        document and updating its record appropriately.

        This process will not be as thorough as one initiated manually
        by the library administrator, but it can be used to
        automatically keep us up to date on minor changes to a
        library's description, logo, etc.

        :param library: A Library.

        :return: A ProblemDetail if there's a problem. Otherwise, None.
        """
        result = self.register(library, library.library_stage)
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

    def register(self, library: Library, library_stage):
        """Register the given Library with this registry, if possible.

        :param library: A Library to register or re-register.
        :param library_stage: The library administrator's proposed value for
            Library.library_stage.

        :return: A ProblemDetail if there's a problem. Otherwise, a 2-tuple
            (auth_document, new_hyperlinks).

        `auth_document` is an AuthenticationDocument corresponding to
            the library's authentication document, as found at auth_url.
        `new_hyperlinks` is a list of Hyperlinks
             that ought to be created for registration to be complete.
        """
        hyperlinks_to_create = []

        auth_url = library.authentication_url
        auth_response = self._make_request(
            auth_url,
            auth_url,
            _("No Authentication For OPDS document present at %(url)s", url=auth_url),
            _("Timeout retrieving auth document %(url)s", url=auth_url),
            _("Error retrieving auth document %(url)s", url=auth_url),
        )
        if isinstance(auth_response, ProblemDetail):
            return auth_response
        try:
            auth_document = AuthenticationDocument.from_string(
                self._db, auth_response.content
            )
        except Exception as e:
            self.log.error(
                "Registration of %s failed: invalid auth document.",
                auth_url,
                exc_info=e,
            )
            return INVALID_INTEGRATION_DOCUMENT
        failure_detail = None
        if not auth_document.id:
            failure_detail = _("The OPDS authentication document is missing an id.")
        if not auth_document.title:
            failure_detail = _("The OPDS authentication document is missing a title.")
        if auth_document.root:
            opds_url = auth_document.root["href"]
        else:
            failure_detail = _(
                "The OPDS authentication document is missing a 'start' link to the root OPDS feed."
            )

        if auth_document.id != auth_response.url:
            failure_detail = _(
                "The OPDS authentication document's id (%(id)s) doesn't match its url (%(url)s).",
                id=auth_document.id,
                url=auth_response.url,
            )
        if failure_detail:
            self.log.error("Registration of %s failed: %s", auth_url, failure_detail)
            return INVALID_INTEGRATION_DOCUMENT.detailed(failure_detail)

        # Make sure the authentication document includes a way for
        # patrons to get help or file a copyright complaint. These
        # links must be stored in the database as Hyperlink objects.
        links = auth_document.links or []
        for rel, problem_title, regexes in [
            (
                "help",
                "Invalid or missing patron support email address or website",
                VerifyLinkRegexes.HTTP_OR_MAILTO,
            ),
            (
                Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL,
                "Invalid or missing copyright designated agent email address",
                VerifyLinkRegexes.MAILTO,
            ),
        ]:
            uris = self._verify_links(rel, links, problem_title, link_regex=regexes)
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
            allow_401=True,
        )
        if isinstance(opds_response, ProblemDetail):
            return opds_response

        content_type = opds_response.headers.get("Content-Type")
        failure_detail = None
        if opds_response.status_code == 401:
            # This is only acceptable if the server returned a copy of
            # the Authentication For OPDS document we just got.
            if content_type != AuthenticationDocument.MEDIA_TYPE:
                failure_detail = _(
                    "401 response at %(url)s did not yield an Authentication For OPDS document",
                    url=opds_url,
                )
            elif not self.opds_response_links_to_auth_document(opds_response, auth_url):
                failure_detail = _(
                    "Authentication For OPDS document guarding %(opds_url)s does not match the one at %(auth_url)s",
                    opds_url=opds_url,
                    auth_url=auth_url,
                )
        elif content_type not in (OPDSCatalog.OPDS_TYPE, OPDSCatalog.OPDS_1_TYPE):
            failure_detail = _(
                "Supposed root document at %(url)s is not an OPDS document",
                url=opds_url,
            )
        elif not self.opds_response_links_to_auth_document(opds_response, auth_url):
            failure_detail = _(
                "OPDS root document at %(opds_url)s does not link back to authentication document %(auth_url)s",
                opds_url=opds_url,
                auth_url=auth_url,
            )

        if failure_detail:
            self.log.error("Registration of %s failed: %s", auth_url, failure_detail)
            return INVALID_INTEGRATION_DOCUMENT.detailed(failure_detail)

        auth_url = auth_response.url

        try:
            library.library_stage = library_stage
        except ValueError:
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
            # Write this data to the storage too
            logo_link = LibraryLogoStore.write_raw(library, auth_document.logo)
            if logo_link:
                library.logo_url = logo_link
            else:
                return INVALID_INTEGRATION_DOCUMENT.detailed(
                    _("Could upload the logo image to the file storage")
                )
        elif auth_document.logo_link:
            url = auth_document.logo_link.get("href")
            if url:
                url = urljoin(opds_url, url)
            logo_response = self.do_get(url, stream=True)
            try:
                image = Image.open(logo_response.raw)
            except Exception:
                image_url = auth_document.logo_link.get("href")
                self.log.error(
                    "Registration of %s failed: could not read logo image %s",
                    auth_url,
                    image_url,
                )
                return INVALID_INTEGRATION_DOCUMENT.detailed(
                    _("Could not read logo image %(image_url)s", image_url=image_url)
                )
            # Convert to PNG.
            buffer = BytesIO()
            image.save(buffer, format="PNG")

            # Upload to the file store
            logo_url = LibraryLogoStore.write(library, buffer)
            if not logo_url:
                return INVALID_INTEGRATION_DOCUMENT.detailed(
                    _("Could upload the logo image to the file storage")
                )
            library.logo_url = logo_url
            buffer.seek(0)

            b64 = base64.b64encode(buffer.getvalue()).decode("utf8")
            type = logo_response.headers.get(
                "Content-Type"
            ) or auth_document.logo_link.get("type")
            if type:
                library.logo = "data:%s;base64,%s" % (type, b64)
        else:
            library.logo = None
        problem = auth_document.update_library(library)
        if problem:
            self.log.error(
                "Registration of %s failed: problem during registration: %s/%s/%s/%s",
                auth_url,
                problem.uri,
                problem.title,
                problem.detail,
                problem.debug_message,
            )
            return problem

        return auth_document, hyperlinks_to_create

    def _make_request(
        self, registration_url, url, on_404, on_timeout, on_exception, allow_401=False
    ):
        allowed_codes = ["2xx", "3xx", 404]
        if allow_401:
            allowed_codes.append(401)
        try:
            response = self.do_get(
                url, allowed_response_codes=allowed_codes, timeout=30
            )
            # We only allowed 404 above so that we could return a more
            # specific problem detail document if it happened.
            if response.status_code == 404:
                return INTEGRATION_DOCUMENT_NOT_FOUND.detailed(on_404)
            if not allow_401 and response.status_code == 401:
                self.log.error(
                    "Registration of %s failed: %s is behind authentication gateway",
                    registration_url,
                    url,
                )
                return ERROR_RETRIEVING_DOCUMENT.detailed(
                    _("%(url)s is behind an authentication gateway", url=url)
                )
        except RequestTimedOut as e:
            self.log.error(
                "Registration of %s failed: timeout retrieving %s",
                registration_url,
                url,
                exc_info=e,
            )
            return TIMEOUT.detailed(on_timeout)
        except Exception as e:
            self.log.error(
                "Registration of %s failed: error retrieving %s",
                registration_url,
                url,
                exc_info=e,
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
            links.append(link.get("url"))
        media_type = response.headers.get("Content-Type")
        if media_type == OPDSCatalog.OPDS_TYPE:
            # Parse as OPDS 2.
            catalog = json.loads(response.content)
            links = []
            for k, v in catalog.get("links", {}).items():
                if k == rel:
                    links.append(v.get("href"))
        elif media_type == OPDSCatalog.OPDS_1_TYPE:
            # Parse as OPDS 1.
            feed = feedparser.parse(response.content)
            for link in feed.get("feed", {}).get("links", []):
                if link.get("rel") == rel:
                    links.append(link.get("href"))
        elif media_type == AuthenticationDocument.MEDIA_TYPE:
            document = json.loads(response.content)
            if isinstance(document, dict):
                links.append(document.get("id"))
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
        except ValueError:
            # The response itself is malformed.
            return False
        return auth_url in links

    @classmethod
    def _verify_links(
        cls, rel, links, problem_title, link_regex=VerifyLinkRegexes.MAILTO
    ):
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
            if link.get("rel") != rel:
                # Wrong kind of link.
                continue
            uri = link.get("href")
            value = cls._required_link_type(uri, problem_title, link_regex)
            if isinstance(value, str):
                candidates.append(value)

        # There were no relevant links.
        if not candidates:
            problem = INVALID_CONTACT_URI.detailed(
                "No valid '%s' links found with rel=%s" % (link_regex, rel)
            )
            problem.title = problem_title
            return problem

        return candidates

    @classmethod
    def _required_link_type(cls, uri, problem_title, link_regex):
        """Verify that `uri` is a particular type URI.

        :return: Either a verified type URI or a customized ProblemDetail.
        """
        problem = None
        on_error = INVALID_CONTACT_URI
        if not uri:
            problem = on_error.detailed("No link href was provided")
        elif not re.match(link_regex, uri):
            problem = on_error.detailed(
                _("URI must match '%s' (got: %s)") % (link_regex, uri)
            )
        if problem:
            problem.title = problem_title
            return problem
        return uri
