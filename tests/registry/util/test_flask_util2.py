import ipaddress
import re

import pytest
from flask import Flask, request

from palace.registry.util.flask_util import (
    IPV4_REGEX,
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
