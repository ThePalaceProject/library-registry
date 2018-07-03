from util.problem_detail import ProblemDetail as pd
from flask_babel import lazy_gettext as _

NO_AUTH_URL = pd(
      "http://librarysimplified.org/terms/problem/no-opds-auth-url",
      400,
      _("No Authentication For OPDS URL"),
      _("You must provide the URL to an Authentication For OPDS document to register a library."),
)

INVALID_INTEGRATION_DOCUMENT = pd(
      "http://librarysimplified.org/terms/problem/invalid-integration-document",
      400,
      _("Invalid Integration document"),
)

TIMEOUT = pd(
      "http://librarysimplified.org/terms/problem/timeout",
      408,
      _("Request timed out"),
      _("Attempt to retrieve an Authentication For OPDS document timed out."),
)


INTEGRATION_DOCUMENT_NOT_FOUND = pd(
    "http://librarysimplified.org/terms/problem/integration-document-not-found",
    400,
    title=_("Document not found"),
)

ERROR_RETRIEVING_DOCUMENT = pd(
    "http://librarysimplified.org/terms/problem/remote-integration-failed",
    502,
    title=_("Could not retrieve document"),
    detail=_("I couldn't retrieve the specified URL."),
)

INVALID_CONTACT_URI = pd(
    "http://librarysimplified.org/terms/problem/invalid-contact-uri",
    400,
    title=_("URI was not specified or is of the wrong type")
)
