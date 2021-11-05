from io import BytesIO

from sqlalchemy.orm.exc import (MultipleResultsFound, NoResultFound)

from library_registry.util.http import BadResponseException


class MockPlace:
    """Used to test AuthenticationDocument.parse_coverage."""
    ##### Class Constants ####################################################  # noqa: E266

    AMBIGUOUS = object()    # Indicates a place name is ambiguous
    EVERYWHERE = object()   # Indicates coverage through universe or a country
    _default_nation = None  # Starting point for place names that don't mention a nation
    by_name = dict()

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    def __init__(self, inside=None):
        self.inside = inside or dict()
        self.abbreviated_name = None

    def lookup_inside(self, name):
        place = self.inside.get(name)

        if place is self.AMBIGUOUS:
            raise MultipleResultsFound()

        if place is None:
            raise NoResultFound()

        return place

    ##### Private Methods ####################################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    @classmethod
    def default_nation(cls, _db):
        return cls._default_nation

    @classmethod
    def lookup_one_by_name(cls, _db, name, place_type):
        place = cls.by_name.get(name)
        if place is cls.AMBIGUOUS:
            raise MultipleResultsFound()
        if place is None:
            raise NoResultFound()
        print("%s->%s" % (name, place))
        return place

    @classmethod
    def everywhere(cls, _db):
        return cls.EVERYWHERE

    ##### Private Class Methods ##############################################  # noqa: E266


class DummyHTTPResponse:
    def __init__(self, status_code, headers, content, links=None, url=None):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.links = links or {}
        self.url = url or "http://url/"

    @property
    def raw(self):
        return BytesIO(self.content)


class DummyHTTPClient:
    def __init__(self):
        self.responses = []
        self.requests = []

    def queue_response(
        self, response_code, media_type="text/html", other_headers=None,
        content='', links=None, url=None
    ):
        headers = {}
        if media_type:
            headers["Content-Type"] = media_type

        if other_headers:
            for k, v in list(other_headers.items()):
                headers[k.lower()] = v

        self.responses.insert(
            0, DummyHTTPResponse(response_code, headers, content, links, url)
        )

    def do_get(self, url, headers=None, allowed_response_codes=None, **kwargs):
        self.requests.append(url)
        response = self.responses.pop()

        if isinstance(response.status_code, Exception):
            raise response.status_code

        # Simulate the behavior of requests, where response.url contains
        # the final URL that responded to the request.
        response.url = url

        code = response.status_code
        series = "%sxx" % (code // 100)

        if (
            allowed_response_codes and (
                code not in allowed_response_codes
                and series not in allowed_response_codes
            )
        ):
            raise BadResponseException(url, "Bad Response!", status_code=code)

        return response
