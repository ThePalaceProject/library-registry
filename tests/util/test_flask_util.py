import pytest
from flask import Flask

from util.flask_util import originating_ip

app = Flask(__name__)


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
def test_originating_ip(expected_address, remote_address, headers):
    with app.test_request_context(
        "url", headers=headers, environ_base={"REMOTE_ADDR": remote_address}
    ):
        result_address = originating_ip()

    assert result_address == expected_address
