from nose.tools import (
    eq_,
    set_trace,
)
from emailer import (
    Emailer,
    EmailTemplate,
)


class TestEmailTemplate(object):
    """Test the ability to generate email messages."""

    def test_body(self):
        template = EmailTemplate(
            "A %(color)s subject",
            "The subject is %(color)s but the body is %(number)d"
        )
        body = template.body("me@example.com", "you@example.com",
                      color="red", number=22
        )
        eq_(
"""From: me@example.com
To: you@example.com
Subject: A red subject

The subject is red but the body is 22""",
            body
        )
