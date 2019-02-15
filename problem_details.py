from util.problem_detail import ProblemDetail as pd
from flask_babel import lazy_gettext as _

AUTHENTICATION_FAILURE = pd(
    "http://librarysimplified.org/terms/problem/credentials-invalid",
    401,
    _("The library could not be authenticated.")
)

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

INTEGRATION_ERROR = pd(
    "http://librarysimplified.org/terms/problem/remote-integration-failed",
    500,
    title=_("Error with external integration"),
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

LIBRARY_ALREADY_IN_PRODUCTION = pd(
    "http://librarysimplified.org/terms/problem/invalid-stage",
    400,
    title=_("Library cannot be taken out of production once in production.")
)

LIBRARY_NOT_FOUND = pd(
    "http://librarysimplified.org/terms/problem/library-not-found",
    404,
    title=_("The library does not exist in this registry."),
)

INVALID_CREDENTIALS = pd(
    "http://librarysimplified.org/terms/problem/invalid-credentials",
    401,
    title=_("The username or password is incorrect.")
)

CANNOT_VALIDATE = pd(
    "http://librarysimplified.org/terms/problem/invalid-credentials",
    500,
    title=_("Unable to validate this email address.")
)
