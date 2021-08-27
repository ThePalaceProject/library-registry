import json

import pytest
import requests

from testing import MockRequestsResponse
from util.http import (
    HTTP,
    BadResponseException,
    RemoteIntegrationException,
    RequestNetworkException,
    RequestTimedOut,
)


class TestHTTP(object):
    def test_request_with_timeout_success(self):
        def fake_200_response(*args, **kwargs):
            return MockRequestsResponse(200, content="Success!")

        response = HTTP._request_with_timeout("the url", fake_200_response, "a", "b")
        assert response.status_code == 200
        assert response.content == "Success!"

    def test_request_with_timeout_failure(self):
        def immediately_timeout(*args, **kwargs):
            raise requests.exceptions.Timeout("I give up")

        with pytest.raises(RequestTimedOut) as exc:
            HTTP._request_with_timeout("http://url/", immediately_timeout, "a", "b")
        assert "Timeout accessing http://url/: I give up" in str(exc.value)

    def test_request_with_network_failure(self):
        def immediately_fail(*args, **kwargs):
            raise requests.exceptions.ConnectionError("a disaster")

        with pytest.raises(RequestNetworkException) as exc:
            HTTP._request_with_timeout("http://url/", immediately_fail, "a", "b")
        assert "Network error contacting http://url/: a disaster" in str(exc.value)

    def test_request_with_response_indicative_of_failure(self):
        def fake_500_response(*args, **kwargs):
            return MockRequestsResponse(500, content="Failure!")

        with pytest.raises(BadResponseException) as exc:
            HTTP._request_with_timeout("http://url/", fake_500_response, "a", "b")
        assert (
            r"Bad response from http://url/: Got status code 500 from external server, cannot continue."
            in str(exc.value)
        )

    def test_allowed_response_codes(self):
        # Test our ability to raise BadResponseException when
        # an HTTP-based integration does not behave as we'd expect.

        def fake_401_response(*args, **kwargs):
            return MockRequestsResponse(401, content="Weird")

        def fake_200_response(*args, **kwargs):
            return MockRequestsResponse(200, content="Hurray")

        url = "http://url/"
        m = HTTP._request_with_timeout

        # By default, every code except for 5xx codes is allowed.
        response = m(url, fake_401_response)
        assert response.status_code == 401

        # You can say that certain codes are specifically allowed, and
        # all others are forbidden.
        with pytest.raises(BadResponseException) as exc:
            m(url, fake_401_response, allowed_response_codes=[201, 200])
        assert "Bad response" in str(exc.value)
        assert (
            "Got status code 401 from external server, but can only continue on: 200, 201."
            in str(exc.value)
        )

        response = m(url, fake_401_response, allowed_response_codes=[401])
        response = m(url, fake_401_response, allowed_response_codes=["4xx"])

        # In this way you can even raise an exception on a 200 response code.
        with pytest.raises(BadResponseException) as exc:
            m(url, fake_200_response, allowed_response_codes=[401])
        assert (
            "Got status code 200 from external server, but can only continue on: 401."
            in str(exc.value)
        )

        # You can say that certain codes are explicitly forbidden, and
        # all others are allowed.
        with pytest.raises(BadResponseException) as exc:
            m(url, fake_401_response, disallowed_response_codes=[401])
        assert "Got status code 401 from external server, cannot continue." in str(
            exc.value
        )

        with pytest.raises(BadResponseException) as exc:
            m(url, fake_200_response, disallowed_response_codes=["2xx", 301])
        assert "Got status code 200 from external server, cannot continue." in str(
            exc.value
        )

        response = m(url, fake_401_response, disallowed_response_codes=["2xx"])
        assert response.status_code == 401

        # The exception can be turned into a useful problem detail document.
        exception = None
        try:
            m(url, fake_200_response, disallowed_response_codes=["2xx"])
        except Exception as e:
            exception = e
        assert exception is not None

        debug_doc = exception.as_problem_detail_document(debug=True)

        # 502 is the status code to be returned if this integration error
        # interrupts the processing of an incoming HTTP request, not the
        # status code that caused the problem.
        #
        assert debug_doc.status_code == 502
        assert debug_doc.title == "Bad response"
        assert (
            debug_doc.detail
            == "The server made a request to http://url/, and got an unexpected or invalid response."
        )
        assert (
            debug_doc.debug_message
            == "Bad response from http://url/: Got status code 200 from external server, cannot continue.\n\nResponse content: Hurray"
        )

        no_debug_doc = exception.as_problem_detail_document(debug=False)
        assert no_debug_doc.title == "Bad response"
        assert (
            no_debug_doc.detail
            == "The server made a request to url, and got an unexpected or invalid response."
        )
        assert no_debug_doc.debug_message is None

    def test_unicode_converted_to_utf8(self):
        """Any Unicode that sneaks into the URL, headers or body is
        converted to UTF-8.
        """

        class ResponseGenerator(object):
            def __init__(self):
                self.requests = []

            def response(self, *args, **kwargs):
                self.requests.append((args, kwargs))
                return MockRequestsResponse(200, content="Success!")

        generator = ResponseGenerator()
        url = "http://foo"
        HTTP._request_with_timeout(
            url,
            generator.response,
            url,
            "POST",
            headers={"unicode header": "unicode value"},
            data="unicode data",
        )
        [(args, kwargs)] = generator.requests
        url, method = args
        headers = kwargs["headers"]
        data = kwargs["data"]

        # All the Unicode data was converted to bytes before being sent
        # "over the wire".
        for k, v in list(headers.items()):
            assert isinstance(k, bytes)
            assert isinstance(v, bytes)
        assert isinstance(data, bytes)


class TestRemoteIntegrationException(object):
    def test_with_service_name(self):
        """You don't have to provide a URL when creating a
        RemoteIntegrationException; you can just provide the service
        name.
        """
        exc = RemoteIntegrationException(
            "Unreliable Service", "I just can't handle your request right now."
        )

        # Since only the service name is provided, there are no details to
        # elide in the non-debug version of a problem detail document.
        debug_detail = exc.document_detail(debug=True)
        other_detail = exc.document_detail(debug=False)
        assert other_detail == debug_detail

        assert (
            debug_detail
            == "The server tried to access Unreliable Service but the third-party service experienced an error."
        )


class TestBadResponseException(object):
    def test_helper_constructor(self):
        response = MockRequestsResponse(102, content="nonsense")
        exc = BadResponseException.from_response(
            "http://url/", "Terrible response, just terrible", response
        )

        # Turn the exception into a problem detail document, and it's full
        # of useful information.
        doc, status_code, headers = exc.as_problem_detail_document(debug=True).response
        doc = json.loads(doc)

        assert doc["title"] == "Bad response"
        assert (
            doc["detail"]
            == "The server made a request to http://url/, and got an unexpected or invalid response."
        )
        assert (
            doc["debug_message"]
            == "Bad response from http://url/: Terrible response, just terrible\n\nStatus code: 102\nContent: nonsense"
        )

        # Unless debug is turned off, in which case none of that
        # information is present.
        doc, status_code, headers = exc.as_problem_detail_document(debug=False).response
        assert "debug_message" not in json.loads(doc)

    def test_bad_status_code_helper(object):
        response = MockRequestsResponse(500, content="Internal Server Error!")
        exc = BadResponseException.bad_status_code("http://url/", response)
        doc, status_code, headers = exc.as_problem_detail_document(debug=True).response
        doc = json.loads(doc)

        assert (
            "Got status code 500 from external server, cannot continue."
            in doc["debug_message"]
        )

    def test_as_problem_detail_document(self):
        exception = BadResponseException(
            "http://url/", "What even is this", debug_message="some debug info"
        )
        document = exception.as_problem_detail_document(debug=True)
        assert document.status_code == 502
        assert document.title == "Bad response"
        assert (
            document.detail
            == "The server made a request to http://url/, and got an unexpected or invalid response."
        )
        assert (
            document.debug_message
            == "Bad response from http://url/: What even is this\n\nsome debug info"
        )


class TestRequestTimedOut(object):
    def test_as_problem_detail_document(self):
        exception = RequestTimedOut("http://url/", "I give up")

        debug_detail = exception.as_problem_detail_document(debug=True)
        assert debug_detail.title == "Timeout"
        assert (
            debug_detail.detail
            == "The server made a request to http://url/, and that request timed out."
        )

        # If we're not in debug mode, we hide the URL we accessed and just
        # show the hostname.
        standard_detail = exception.as_problem_detail_document(debug=False)
        assert (
            standard_detail.detail
            == "The server made a request to url, and that request timed out."
        )

        # The status code corresponding to an upstream timeout is 502.
        document, status_code, headers = standard_detail.response
        assert status_code == 502


class TestRequestNetworkException(object):
    def test_as_problem_detail_document(self):
        exception = RequestNetworkException("http://url/", "Colossal failure")

        debug_detail = exception.as_problem_detail_document(debug=True)
        assert debug_detail.title == "Network failure contacting third-party service"
        assert (
            debug_detail.detail
            == "The server experienced a network error while contacting http://url/."
        )

        # If we're not in debug mode, we hide the URL we accessed and just
        # show the hostname.
        standard_detail = exception.as_problem_detail_document(debug=False)
        assert (
            standard_detail.detail
            == "The server experienced a network error while contacting url."
        )

        # The status code corresponding to an upstream timeout is 502.
        document, status_code, headers = standard_detail.response
        assert status_code == 502
