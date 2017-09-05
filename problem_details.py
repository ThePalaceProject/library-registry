from util.problem_detail import ProblemDetail as pd
from flask.ext.babel import lazy_gettext as _

NO_AUTH_URL = pd(
      "http://librarysimplified.org/terms/problem/no-opds-auth-url",
      400,
      _("No Authentication For OPDS URL"),
      _("You must provide the URL to an Authentication For OPDS document to register a library."),
)

INVALID_AUTH_DOCUMENT = pd(
      "http://librarysimplified.org/terms/problem/invalid-opds-auth-document",
      400,
      _("Invalid Authentication For OPDS document"),
      _("The submitted URL did not return a valid Authentication For OPDS document."),
)

AUTH_DOCUMENT_TIMEOUT = pd(
      "http://librarysimplified.org/terms/problem/timeout",
      408,
      _("Request timed out"),
      _("Attempt to retrieve an Authentication For OPDS document timed out."),
)


AUTH_DOCUMENT_NOT_FOUND = pd(
    "http://librarysimplified.org/terms/problem/auth-document-not-found",
    400,
    title=_("Authentication document not found"),
    detail=_("No OPDS authentication document was present at the specified URL."),
)

ERROR_RETRIEVING_AUTH_DOCUMENT = pd(
    "http://librarysimplified.org/terms/problem/remote-integration-failed",
    502,
    title=_("Could not retrieve authentication document"),
    detail=_("I couldn't retrieve an authentication document from the specified URL."),
)

