from util.problem_detail import ProblemDetail as pd
from flask.ext.babel import lazy_gettext as _

NO_OPDS_URL = pd(
      "http://librarysimplified.org/terms/problem/no-opds-url",
      400,
      _("No OPDS URL"),
      _("You must provide an OPDS URL to register a library."),
)

INVALID_OPDS_FEED = pd(
      "http://librarysimplified.org/terms/problem/invalid-opds-feed",
      400,
      _("Invalid OPDS feed"),
      _("The submitted URL did not return a valid OPDS feed."),
)

NO_SHELF_LINK = pd(
    "http://librarysimplified.org/terms/problem/no-shelf-link",
    400,
    title=_("No shelf link"),
    detail=_("The submitted OPDS feed did not have a link with rel 'http://opds-spec.org/shelf'.")
)

INVALID_AUTH_DOCUMENT = pd(
    "http://librarysimplified.org/terms/problem/invalid-auth-document",
    400,
    title=_("Invalid auth document"),
    detail=_("The OPDS authentication document is not valid JSON."),
)
