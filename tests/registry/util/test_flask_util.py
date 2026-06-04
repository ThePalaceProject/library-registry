import datetime
import ipaddress
import re

import pytest
from flask import Flask, Response, request

from palace.registry.util.flask_util import (
    IPV4_REGEX,
    deprecated,
    is_public_ipv4_address,
    originating_ip,
)


@pytest.fixture
def generic_app_obj():
    return Flask(__name__)


class TestFlaskUtil:
    @pytest.mark.parametrize(
        "ip_string,result",
        [
            pytest.param("127.0.0.1", ["127.0.0.1"], id="single_valid_ip"),
            pytest.param("300.0.0.1", [], id="invalid_ip_quad_one"),
            pytest.param("200.300.0.1", [], id="invalid_ip_quad_two"),
            pytest.param("200.0.300.1", [], id="invalid_ip_quad_three"),
            pytest.param("200.0.0.300", [], id="invalid_ip_quad_four"),
            pytest.param("300.300.300.300", [], id="invalid_ip_all_quads"),
            pytest.param(
                "abc200.100.100.5abc",
                ["200.100.100.5"],
                id="non_numeric_prefix_and_postfix",
            ),
            pytest.param(
                "300.300.300.300 100.100.100.100",
                ["100.100.100.100"],
                id="mixed_valid_and_invalid",
            ),
            pytest.param(
                "64.234.82.200 and also 75.245.93.211",
                ["64.234.82.200", "75.245.93.211"],
                id="words",
            ),
            pytest.param(
                "64.234.82.200,75.245.93.211",
                ["64.234.82.200", "75.245.93.211"],
                id="comma_sep_valid_ips",
            ),
            pytest.param(
                "64.234.82.200 75.245.93.211",
                ["64.234.82.200", "75.245.93.211"],
                id="space_sep_valid_ips",
            ),
            pytest.param(
                "64.234.82.200, 75.245.93.211",
                ["64.234.82.200", "75.245.93.211"],
                id="multi_sep_valid_ips",
            ),
        ],
    )
    def test_ipv4_regex(self, ip_string, result):
        """
        GIVEN: A string which may contain one or more IPv4 addresses in dot-separated quad form
        WHEN:  re.findall(IPV4_REGEX, ip_string) is called
        THEN:  A list returning all valid IPv4 addresses extracted from the string is returned
        """
        assert re.findall(IPV4_REGEX, ip_string) == result

    @pytest.mark.parametrize(
        "fwd4_value,remote_addr,result",
        [
            pytest.param(
                "64.234.82.200",
                "64.234.82.201",
                "64.234.82.200",
                id="ip_from_Fwd4_header",
            ),
            pytest.param(
                "10.0.0.1", "64.234.82.200", "64.234.82.200", id="ip_from_remote_addr"
            ),
            pytest.param("10.0.0.1", "10.0.0.2", None, id="no_public_ip_provided"),
            pytest.param(
                "64.234.82.200, 10.0.0.1",
                "64.234.82.201",
                "64.234.82.200",
                id="multival_Fwd4",
            ),
        ],
    )
    def test_originating_ip(self, generic_app_obj, fwd4_value, remote_addr, result):
        """
        GIVEN: A request object with values in the 'X-Forwarded-For' header and remote_addr
        WHEN:  originating_ip() is called
        THEN:  The appropriate value (an IP address or None) should be returned
        """
        fwd4_header = "X-Forwarded-For"
        headers = {fwd4_header.upper(): fwd4_value}
        env_base = {"REMOTE_ADDR": remote_addr}
        with generic_app_obj.test_request_context(
            headers=headers, environ_base=env_base
        ):
            assert request.headers.get(fwd4_header) == fwd4_value
            assert request.remote_addr == remote_addr

            if result is None or isinstance(result, bool):
                assert originating_ip() is result
            else:
                assert originating_ip() == result

    @pytest.mark.parametrize(
        "address,result",
        [
            (ipaddress.ip_address("64.234.82.200"), True),  # IPv4Address object arg
            ("not an ip address", None),  # Bad input
            ("10.0.0.1", False),  # Private
            ("224.0.0.3", False),  # Multicast
            ("0.0.0.0", False),  # Unspecified
            ("240.0.0.1", False),  # Reserved
            ("127.0.0.1", False),  # Loopback
            ("169.254.0.1", False),  # Link local
            ("255.255.255.255", False),  # Broadcast
            ("64.234.82.200", True),  # Public
        ],
    )
    def test_is_public_ipv4_address(self, address, result):
        """
        GIVEN: A string representation of an IPv4 address
        WHEN:  is_public_ipv4_address() is called on that string
        THEN:  The appropriate boolean response should be returned
        """
        if not result or isinstance(result, bool):
            assert is_public_ipv4_address(address) is result
        else:
            assert is_public_ipv4_address(address) == result

    @pytest.mark.parametrize(
        "expected_address, remote_address, headers",
        [
            (None, "192.168.1.10", {}),
            ("128.128.1.10", "128.128.1.10", {}),
            (None, "192.168.1.10", {"X-Forwarded-For": ""}),
            ("128.128.1.10", "128.128.1.10", {"X-Forwarded-For": ""}),
            ("128.128.1.10", "192.168.1.10", {"X-Forwarded-For": "128.128.1.10"}),
            (
                "128.128.1.10",
                "192.168.1.10",
                {"X-Forwarded-For": "128.128.1.10,192.168.1.20"},
            ),
            (
                "128.128.1.10",
                "192.168.1.10",
                {"x-forwarded-for": "192.168.2.20, 128.128.1.10"},
            ),
            (
                "128.128.1.10",
                "192.168.1.10",
                {"x-forwarded-for": "192.168.2.20, 192.168.1.20, 128.128.1.10"},
            ),
        ],
    )
    def test_originating_ip(
        self, generic_app_obj, expected_address, remote_address, headers
    ):
        with generic_app_obj.test_request_context(
            "url", headers=headers, environ_base={"REMOTE_ADDR": remote_address}
        ):
            assert originating_ip() == expected_address


class TestDeprecated:
    """Tests for the deprecated() route decorator."""

    def _route(self, **kwargs):
        """Return a decorated route function using the given deprecated() kwargs."""

        @deprecated(**kwargs)
        def route():
            return Response("ok", 200)

        return route

    @pytest.mark.parametrize(
        "deprecation_date, expected",
        [
            pytest.param(None, "true", id="no-date"),
            pytest.param(
                datetime.datetime(2025, 6, 1, tzinfo=datetime.UTC),
                "Sun, 01 Jun 2025 00:00:00 GMT",
                id="with-date",
            ),
        ],
    )
    def test_deprecation_header_value(
        self, generic_app_obj, deprecation_date, expected
    ):
        """Deprecation header is 'true' when no date is given, or an IMF-fixdate (RFC 9745 §2)."""
        route = self._route(deprecation_date=deprecation_date)
        with generic_app_obj.test_request_context("/"):
            assert route().headers["Deprecation"] == expected

    def test_sunset_header_when_provided(self, generic_app_obj):
        """Sunset header is emitted when sunset is given."""
        date = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        route = self._route(sunset=date)
        with generic_app_obj.test_request_context("/"):
            assert route().headers["Sunset"] == "Thu, 01 Jan 2026 00:00:00 GMT"

    def test_no_sunset_header_when_omitted(self, generic_app_obj):
        """Sunset header is absent when no sunset date is given."""
        route = self._route()
        with generic_app_obj.test_request_context("/"):
            assert "Sunset" not in route().headers

    @pytest.mark.parametrize(
        "kwarg, path, expected_rel",
        [
            pytest.param(
                "documentation",
                "/deprecation-info",
                'rel="deprecation"',
                id="documentation",
            ),
            pytest.param(
                "replacement",
                "/new?x=1",
                'rel="successor-version"',
                id="replacement",
            ),
        ],
    )
    def test_link_header_when_provided(
        self, generic_app_obj, kwarg, path, expected_rel
    ):
        """Link header is emitted for documentation (RFC 9745 §3.1) and replacement (RFC 5829)."""
        route = self._route(**{kwarg: path})
        with generic_app_obj.test_request_context("/"):
            link = route().headers["Link"]
        assert expected_rel in link
        assert f"http://localhost{path}" in link

    def test_both_link_parts_combined_in_single_header(self, generic_app_obj):
        """documentation and replacement are combined into a single Link header."""
        route = self._route(documentation="/docs", replacement="/new")
        with generic_app_obj.test_request_context("/"):
            link = route().headers["Link"]
        assert 'rel="deprecation"' in link
        assert 'rel="successor-version"' in link
        # Both must appear in the same header value, not as separate headers.
        assert isinstance(link, str)

    def test_existing_link_header_is_preserved(self, generic_app_obj):
        """Pre-existing Link header values are not clobbered."""

        @deprecated(replacement="/new")
        def route():
            r = Response("ok", 200)
            r.headers["Link"] = '</existing>; rel="related"'
            return r

        with generic_app_obj.test_request_context("/"):
            links = " ".join(route().headers.getlist("Link"))
        assert 'rel="related"' in links
        assert 'rel="successor-version"' in links

    def test_multiple_existing_link_headers_are_preserved(self, generic_app_obj):
        """All pre-existing Link headers survive when the response carries more than one."""

        @deprecated(replacement="/new")
        def route():
            r = Response("ok", 200)
            r.headers.add("Link", '</existing1>; rel="related"')
            r.headers.add("Link", '</existing2>; rel="prev"')
            return r

        with generic_app_obj.test_request_context("/"):
            links = " ".join(route().headers.getlist("Link"))
        assert 'rel="related"' in links
        assert 'rel="prev"' in links
        assert 'rel="successor-version"' in links

    def test_applies_to_error_responses(self, generic_app_obj):
        """Deprecation is a property of the endpoint, not the result — added to error responses too."""

        @deprecated()
        def route():
            return Response("not found", 404)

        with generic_app_obj.test_request_context("/"):
            assert route().headers["Deprecation"] == "true"

    def test_applies_to_non_response_return(self, generic_app_obj):
        """Headers are injected even when the route returns a plain string instead of a Response."""

        @deprecated()
        def route():
            return "ok"

        with generic_app_obj.test_request_context("/"):
            assert route().headers["Deprecation"] == "true"

    @pytest.mark.parametrize(
        "kwarg",
        [
            pytest.param("deprecation_date", id="deprecation-date"),
            pytest.param("sunset", id="sunset"),
        ],
    )
    def test_naive_datetime_raises(self, kwarg):
        """Timezone-naive datetimes are rejected at decoration time."""
        naive = datetime.datetime(2025, 6, 1)
        with pytest.raises(ValueError, match="timezone-aware"):
            deprecated(**{kwarg: naive})
