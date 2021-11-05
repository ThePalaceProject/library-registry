"""
Simple helper library for generating problem detail documents.

As per http://datatracker.ietf.org/doc/draft-ietf-appsawg-http-problem/
"""
import json as j
import logging

from flask_babel import LazyString

from library_registry.constants import PROBLEM_DETAIL_JSON_MEDIA_TYPE


def json(type, status, title, detail=None, instance=None, debug_message=None):
    d = dict(type=type, title=str(title), status=status)
    if detail:
        d['detail'] = str(detail)

    if instance:
        d['instance'] = instance

    if debug_message:
        d['debug_message'] = debug_message

    return j.dumps(d)


class ProblemDetail:
    """A common type of problem."""
    ##### Class Constants ####################################################  # noqa: E266
    JSON_MEDIA_TYPE = PROBLEM_DETAIL_JSON_MEDIA_TYPE

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    def __init__(self, uri, status_code=None, title=None, detail=None,
                 instance=None, debug_message=None):
        self.uri = uri
        self.title = title
        self.status_code = status_code
        self.detail = detail
        self.instance = instance
        self.debug_message = debug_message

    def detailed(self, detail, status_code=None, title=None, instance=None,
                 debug_message=None):
        """
        Create a ProblemDetail for a more specific occurance of an existing ProblemDetail.

        The detailed error message will be shown to patrons.
        """

        # Title and detail must be LazyStrings from Flask-Babel that are
        # localized when they are first used as strings.
        if title and not isinstance(title, LazyString):
            logging.warning("\"%s\" has not been internationalized" % title)

        if detail and not isinstance(detail, LazyString):
            logging.warning("\"%s\" has not been internationalized" % detail)

        return ProblemDetail(
            self.uri,
            status_code or self.status_code,
            title or self.title,
            detail,
            instance,
            debug_message
        )

    def with_debug(self, debug_message, detail=None, status_code=None, title=None, instance=None):
        """
        Insert debugging information into a ProblemDetail.

        The original ProblemDetail's error message will be shown to patrons, but a more specific
        error message will be visible to those who inspect the problem document.
        """
        return ProblemDetail(
            self.uri,
            status_code or self.status_code,
            title or self.title,
            detail or self.detail,
            instance or self.instance,
            debug_message
        )

    ##### Private Methods ####################################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    @property
    def response(self):
        """Create a Flask-style response."""
        return (
            json(
                self.uri, self.status_code, self.title, self.detail,
                self.instance, self.debug_message
            ),
            self.status_code or 400,
            {"Content-Type": self.JSON_MEDIA_TYPE}
        )

    ##### Class Methods ######################################################  # noqa: E266

    ##### Private Class Methods ##############################################  # noqa: E266
