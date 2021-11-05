import quopri
from email.mime.text import MIMEText

import pytest

from library_registry.config import CannotLoadConfiguration, CannotSendEmail
from library_registry.emailer import (Emailer, EmailTemplate)


class TestEmailTemplate:
    """Test the ability to generate email messages."""

    def test_body(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        template = EmailTemplate("A %(color)s subject", "The subject is %(color)s but the body is %(number)d")
        body = template.body("me@example.com", "you@example.com", color="red", number=22)

        # We always generate a MIME multipart message because that's how we handle non-ASCII characters.
        for expect in ("Content-Type: multipart/mixed;", "Content-Transfer-Encoding: quoted-printable"):
            assert expect in body

        # A MIME multipart message contains a randomly generated component, so we can't check the exact
        # contents, but we can verify that the email addresses made it into the From: and To: headers,
        # and that variables were interpolated into the templates.
        for expect in (
            "From: me@example.com\nTo: you@example.com",
            "Subject: =?utf-8?q?A_red_subject",
            "\n\nThe subject is red but the body is 22"
        ):
            assert expect in body

    def test_unicode_quoted_printable(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        # Create an email message that includes Unicode characters in its subject and body.
        snowman = "\N{SNOWMAN}"
        template = EmailTemplate("A snowman for you! %s" % snowman, "Here he is: %s" % snowman)
        body = template.body("me@example.com", "you@example.com")

        # The SNOWMAN character is encoded as quoted-printable in both the subject and the message contents.
        quoted_printable_snowman = quopri.encodestring(snowman.encode("utf8")).decode("utf8")

        for template in (
            "Subject: =?utf-8?q?A_snowman_for_you!_%(snowman)s?=",
            "\n\nHere he is: %(snowman)s"
        ):
            expect = template % dict(snowman=quoted_printable_snowman)
            assert expect in body


class MockSMTP:
    """Mock of smtplib.SMTP that records all incoming calls."""

    calls = []

    def __getattr__(self, method, *args, **kwargs):
        """When asked for a method, return a function that simply records the method call."""
        def record(*args, **kwargs):
            self.calls.append((method, args, kwargs))

        return record


class MockEmailer(Emailer):
    """Store outgoing emails in a list."""
    emails = []

    def _send_email(self, to_address, body, smtp):
        self.emails.append((to_address, body, smtp))


class MockBrokenEmailer(Emailer):
    """Raise a generic Exception when _send_email() is called"""

    def _send_email(*args):
        raise Exception("message from MockBrokenEmailer")


@pytest.fixture
def sitewide_email_integration(db_session, create_test_external_integration):
    integration = create_test_external_integration(db_session)
    integration.goal = Emailer.GOAL
    integration.username = "smtp_username"
    integration.password = "smtp_password"
    integration.url = "smtp_host"
    integration.setting(Emailer.PORT).value = '234'
    integration.setting(Emailer.FROM_NAME).value = 'Me'
    integration.setting(Emailer.FROM_ADDRESS).value = 'me@registry'
    yield integration
    db_session.delete(integration)
    db_session.commit()


class TestEmailer:
    def test__sitewide_integration_missing(self, db_session):
        """
        GIVEN: A database with no integration whose goal is Emailer.GOAL
        WHEN:  Emailer.sitewide_integration is called
        THEN:  CannotLoadConfiguration should be raised
        """
        with pytest.raises(CannotLoadConfiguration) as exc:
            Emailer._sitewide_integration(db_session)

        assert 'No email integration is configured' in str(exc.value)

    def test__sitewide_integration_single_mailer_found(self, db_session, sitewide_email_integration):
        """
        GIVEN: A database with one integration whose goal is Emailer.GOAL
        WHEN:  Emailer._sitewide_integration is called
        THEN:  That integration should be returned
        """
        assert Emailer._sitewide_integration(db_session) == sitewide_email_integration

    def test__sitewide_integration_multiple_mailers_found(
        self, db_session, create_test_external_integration, sitewide_email_integration
    ):
        """
        GIVEN: A database with multiple integrations whose goals are Emailer.GOAL
        WHEN:  Emailer._sitewide_integration is called
        THEN:  CannotLoadConfiguration should be raised
        """
        # If there are multiple integrations with goal=Emailer.GOAL, no sitewide configuration can be determined.
        integration2 = create_test_external_integration(db_session, goal=Emailer.GOAL, protocol="TEST")
        with pytest.raises(CannotLoadConfiguration) as exc:
            Emailer._sitewide_integration(db_session)
        assert 'Multiple email integrations are configured' in str(exc.value)

        db_session.delete(integration2)
        db_session.commit()

    def test_from_sitewide_integration(self, db_session, sitewide_email_integration):
        """Test the ability to load an Emailer from a sitewide integration."""
        integration = sitewide_email_integration
        emailer = Emailer.from_sitewide_integration(db_session)

        # The Emailer's configuration is based on the sitewide integration.
        assert emailer.smtp_username == "smtp_username"
        assert emailer.smtp_password == "smtp_password"
        assert emailer.smtp_host == "smtp_host"
        assert emailer.from_address == "me@registry"

        # Default EmailTemplates have been created for all known email types.
        for email_type in Emailer.EMAIL_TYPES:
            template = emailer.templates[email_type]
            assert template.subject_template == Emailer.SUBJECTS[email_type]
            assert template.body_template == Emailer.BODIES[email_type]

        # Configure custom subject lines and body templates for the known email types, and build another Emailer.
        for email_type in Emailer.EMAIL_TYPES:
            integration.setting(email_type + "_subject").value = ("subject %s" % email_type)
            integration.setting(email_type + "_body").value = ("body %s" % email_type)

        emailer = Emailer.from_sitewide_integration(db_session)

        for email_type in Emailer.EMAIL_TYPES:
            template = emailer.templates[email_type]
            assert template.subject_template == "subject %s" % email_type
            assert template.body_template == "body %s" % email_type

    def test_constructor(self):
        """
        Verify the exceptions raised when required constructor arguments are missing.

        GIVEN:
        WHEN:
        THEN:
        """
        fields = ['smtp_username', 'smtp_password', 'smtp_host', 'smtp_port', 'from_name', 'from_address']
        args = {field: None for field in fields}
        args['templates'] = {}

        with pytest.raises(CannotLoadConfiguration) as exc:
            Emailer(**args)

        assert "Emailer instantiated with missing params" in str(exc.value)
        assert 'smtp_username' in str(exc.value)
        assert 'smtp_password' in str(exc.value)
        assert 'smtp_host' in str(exc.value)
        assert 'smtp_port' in str(exc.value)
        assert 'from_name' in str(exc.value)
        assert 'from_address' in str(exc.value)

        args['smtp_username'] = 'user'
        args['smtp_password'] = 'password'
        args['smtp_host'] = 'host'
        args['smtp_port'] = 'port'
        args['from_name'] = 'Email Sender'
        args['from_address'] = 'from@library.org'

        # With all the arguments specified, it works.
        Emailer(**args)

        # If one of the templates can't be used, it doesn't work.
        args['templates']['key'] = EmailTemplate("%(nope)s", "email body")
        with pytest.raises(CannotLoadConfiguration) as exc:
            Emailer(**args)

        assert r"Template '%(nope)s'/'email body' contains unrecognized key: 'nope'" in str(exc.value)

    def test_templates(self, db_session, sitewide_email_integration):
        """
        Test the emails generated by the default templates.

        GIVEN:
        WHEN:
        THEN:
        """
        emailer = Emailer.from_sitewide_integration(db_session)

        # Start with arguments common to both email templates.
        args = {
            "rel_desc": "support address",
            "library": "My Public Library",
            "library_web_url": "https://library/",
        }

        # Generate the address-designation template.
        designation_template = emailer.templates[Emailer.ADDRESS_DESIGNATED]
        body = designation_template.body("me@registry", "you@library", **args)

        # Verify that the headers were set correctly.
        for phrase in [
            "From: me@registry",
            "To: you@library",
            "This address designated as the support address for My Public".replace(" ", "_"),  # Part of encoding
        ]:
            assert phrase in body

        # Verify that the body was set correctly.
        expect = (
            "This email address, you@library, has been registered with the Library Simplified library registry "
            "as the support address for the library My Public Library (https://library/)."
            "\n\n"
            "If this is obviously wrong (for instance, you don't work at a public library), please accept our "
            "apologies and contact the Library Simplified support address at me@registry -- something has gone wrong."
            "\n\n"
            "If you do work at a public library, but you're not sure what this means, please speak to a technical "
            "point of contact at your library, or contact the Library Simplified support address at me@registry."
        )
        text_part = MIMEText(expect, 'plain', 'utf-8')
        assert text_part.get_payload() in body

        # The confirmation template has a couple extra fields that need filling in.
        confirmation_template = emailer.templates[Emailer.ADDRESS_NEEDS_CONFIRMATION]
        args['confirmation_link'] = "http://registry/confirm"
        body2 = confirmation_template.body("me@registry", "you@library", **args)

        # Verify the subject line
        assert "Confirm_the_" in body2

        # Verify that the extra content is there. (TODO: I wasn't able to check the whole thing because
        # expect2 parses into a slightly different Message object than is generated by Emailer.)
        for phrase in ["\nhttp://registry/confirm\n", "The link will expire"]:
            assert phrase in body2

    def test_send(self, db_session, sitewide_email_integration):
        """
        Validate our ability to construct and send email.

        GIVEN:
        WHEN:
        THEN:
        """
        integration = sitewide_email_integration
        integration.setting("email1_subject").value = "subject %(arg)s"
        integration.setting("email1_body").value = "body %(arg)s"

        emailer = MockEmailer.from_sitewide_integration(db_session)
        emailer.templates['email1'] = EmailTemplate(
            "subject %(arg)s", "Hello, %(to_address)s, this is %(from_address)s."
        )
        mock_smtp = object()

        # Send an email using the template we just created.
        emailer.send("email1", "you@library", mock_smtp, arg="Value")

        # The template was filled out and passed into our mocked-up _send_email implementation.
        (to, body, smtp) = emailer.emails.pop()
        assert to == "you@library"
        for phrase in [
            "From: Me <me@registry>",
            "To: you@library",
            "subject Value".replace(" ", "_"),  # Part of the encoding process.
            "Hello, you@library, this is me@registry."
        ]:
            print(phrase)
            assert phrase in body
        assert smtp == mock_smtp

    def test_send_failure(self, db_session, sitewide_email_integration):
        """
        GIVEN: An Emailer whose _send_email method raises an Exception
        WHEN:  The send() method catches that exception
        THEN:  A more specific exception should be raised
        """
        emailer = MockBrokenEmailer.from_sitewide_integration(db_session)
        emailer.templates['some_email'] = EmailTemplate("subject", "Hello.")
        with pytest.raises(CannotSendEmail):
            emailer.send("some_email", "me@domain.tld")

    def test__send_email(self, db_session, sitewide_email_integration):
        """Verify that send_email calls certain methods on smtplib.SMTP."""
        emailer = Emailer.from_sitewide_integration(db_session)
        mock = MockSMTP()
        emailer._send_email("you@library", "email body", mock)

        # Five smtplib.SMTP methods were called.
        (connect, starttls, login, sendmail, quit) = mock.calls
        assert connect == ('connect', (emailer.smtp_host, emailer.smtp_port), {})
        assert starttls == ('starttls', (), {})
        assert login == ('login', (emailer.smtp_username, emailer.smtp_password), {})
        assert sendmail == ('sendmail', (emailer.from_address, "you@library", "email body"), {})
        assert quit == ("quit", (), {})
