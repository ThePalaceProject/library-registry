from flask_babel import lazy_gettext as lgt

from util.problem_detail import ProblemDetail as pd

AUTHENTICATION_FAILURE = pd(
    "http://librarysimplified.org/terms/problem/credentials-invalid",
    401,
    lgt("The library could not be authenticated."),
)

NO_AUTH_URL = pd(
    "http://librarysimplified.org/terms/problem/no-opds-auth-url",
    400,
    lgt("No Authentication For OPDS URL"),
    lgt(
        "You must provide the URL to an Authentication For OPDS document to register a library."
    ),
)

INVALID_INTEGRATION_DOCUMENT = pd(
    "http://librarysimplified.org/terms/problem/invalid-integration-document",
    400,
    lgt("Invalid Integration document"),
)

TIMEOUT = pd(
    "http://librarysimplified.org/terms/problem/timeout",
    408,
    lgt("Request timed out"),
    lgt("Attempt to retrieve an Authentication For OPDS document timed out."),
)


INTEGRATION_DOCUMENT_NOT_FOUND = pd(
    "http://librarysimplified.org/terms/problem/integration-document-not-found",
    400,
    title=lgt("Document not found"),
)

INTEGRATION_ERROR = pd(
    "http://librarysimplified.org/terms/problem/remote-integration-failed",
    500,
    title=lgt("Error with external integration"),
)

ERROR_RETRIEVING_DOCUMENT = pd(
    "http://librarysimplified.org/terms/problem/remote-integration-failed",
    502,
    title=lgt("Could not retrieve document"),
    detail=lgt("I couldn't retrieve the specified URL."),
)

INVALID_CONTACT_URI = pd(
    "http://librarysimplified.org/terms/problem/invalid-contact-uri",
    400,
    title=lgt("URI was not specified or is of the wrong type"),
)

LIBRARY_ALREADY_IN_PRODUCTION = pd(
    "http://librarysimplified.org/terms/problem/invalid-stage",
    400,
    title=lgt("Library cannot be taken out of production once in production."),
)

LIBRARY_NOT_FOUND = pd(
    "http://librarysimplified.org/terms/problem/library-not-found",
    404,
    title=lgt("The library does not exist in this registry."),
)

INVALID_CREDENTIALS = pd(
    "http://librarysimplified.org/terms/problem/invalid-credentials",
    401,
    title=lgt("The username or password is incorrect."),
)

UNABLE_TO_NOTIFY = pd(
    "http://librarysimplified.org/terms/problem/unable-to-notify",
    500,
    title=lgt("Registry server unable to send notification emails."),
)
