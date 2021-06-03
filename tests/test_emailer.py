from email.mime.text import MIMEText
import quopri

import pytest

from . import DatabaseTest
from config import CannotLoadConfiguration
from emailer import Emailer, EmailTemplate


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

        # We always generate a MIME multipart message because
        # that's how we handle non-ASCII characters.
        for expect in (
                "Content-Type: multipart/mixed;",
                "Content-Transfer-Encoding: quoted-printable"
        ):
            assert expect in body

        # A MIME multipart message contains a randomly generated
        # component, so we can't check the exact contents, but we can
        # verify that the email addresses made it into the From: and
        # To: headers, and that variables were interpolated into the
        # templates.
        for expect in (
            "From: me@example.com\nTo: you@example.com",
            "Subject: =?utf-8?q?A_red_subject",
            "\n\nThe subject is red but the body is 22"
        ):
            assert expect in body


    def test_unicode_quoted_printable(self):
        # Create an email message that includes Unicode characters in
        # its subject and body.
        snowman = "\N{SNOWMAN}"
        template = EmailTemplate(
            "A snowman for you! %s" % snowman,
            "Here he is: %s" % snowman
        )
        body = template.body("me@example.com", "you@example.com")
        # The SNOWMAN character is encoded as quoted-printable in both
        # the subject and the message contents.
        quoted_printable_snowman = quopri.encodestring(
            snowman.encode("utf8")
        ).decode("utf8")
        for template in (
            "Subject: =?utf-8?q?A_snowman_for_you!_%(snowman)s?=",
            "\n\nHere he is: %(snowman)s"
        ):
            expect = template % dict(snowman=quoted_printable_snowman)
            assert expect in body


class MockSMTP(object):
    """Mock of smtplib.SMTP that records all incoming calls."""

    calls = []

    def __init__(self, *args, **kwargs):
        self.calls.append(('__init__', args, kwargs))
        super().__init__()

    def __getattr__(self, method, *args, **kwargs):
        """When asked for a method, return a function that simply records the
        method call.
        """
        def record(*args, **kwargs):
            self.calls.append((method, args, kwargs))
        return record


class MockEmailer(Emailer):
    """Store outgoing emails in a list."""
    emails = []
    def _send_email(self, to_address, body, smtp):
        self.emails.append((to_address, body, smtp))


class TestEmailer(DatabaseTest):

    def _integration(self):
        """Configure a complete sitewide email integration."""
        integration = self._external_integration("my protocol")
        integration.goal = Emailer.GOAL
        integration.username = "smtp_username"
        integration.password = "smtp_password"
        integration.url = "smtp_host"
        integration.setting(Emailer.PORT).value = '234'
        integration.setting(Emailer.FROM_NAME).value = 'Me'
        integration.setting(Emailer.FROM_ADDRESS).value = 'me@registry'
        return integration

    def test__sitewide_integration(self):
        """Test the ability to find a sitewide integration for sending out
        email.
        """
        m = Emailer._sitewide_integration
        # If there's no integration with goal=Emailer.GOAL,
        # _sitewide_integration raises an exception.
        with pytest.raises(CannotLoadConfiguration) as exc:
            m(self._db)
        assert 'No email integration is configured' in str(exc.value)

        # If there's only one, _sitewide_integration finds it.
        integration = self._integration()
        assert m(self._db) == integration

        # If there are multiple integrations with goal=Emailer.GOAL, no
        # sitewide configuration can be determined.
        duplicate = self._integration()
        with pytest.raises(CannotLoadConfiguration) as exc:
            m(self._db)
        assert 'Multiple email integrations are configured' in str(exc.value)

    def test_from_sitewide_integration(self):
        """Test the ability to load an Emailer from a sitewide integration."""
        integration = self._integration()
        emailer = Emailer.from_sitewide_integration(self._db)

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

        # Configure custom subject lines and body templates for the
        # known email types, and build another Emailer.
        for email_type in Emailer.EMAIL_TYPES:
            integration.setting(email_type + "_subject").value = (
                "subject %s" % email_type
            )
            integration.setting(email_type + "_body").value = (
                "body %s" % email_type
            )
        emailer = Emailer.from_sitewide_integration(self._db)
        for email_type in Emailer.EMAIL_TYPES:
            template = emailer.templates[email_type]
            assert template.subject_template == "subject %s" % email_type
            assert template.body_template == "body %s" % email_type

    def test_constructor(self):
        """Verify the exceptions raised when required constructor
        arguments are missing.
        """
        args = dict(
            [(x, None) for x in (
                'smtp_username', 'smtp_password', 'smtp_host', 'smtp_port',
                'from_name', 'from_address',
            )]
        )
        args['templates'] = {}

        m = Emailer
        with pytest.raises(CannotLoadConfiguration) as exc:
            m(**args)
        assert "No SMTP username specified" in str(exc.value)

        args['smtp_username'] = 'user'

        with pytest.raises(CannotLoadConfiguration) as exc:
            m(**args)
        assert "No SMTP password specified" in str(exc.value)

        args['smtp_password'] = 'password'

        with pytest.raises(CannotLoadConfiguration) as exc:
            m(**args)
        assert "No SMTP host specified" in str(exc.value)

        args['smtp_host'] = 'host'

        with pytest.raises(CannotLoadConfiguration) as exc:
            m(**args)
        assert "No SMTP port specified" in str(exc.value)

        args['smtp_port'] = 'port'

        with pytest.raises(CannotLoadConfiguration) as exc:
            m(**args)
        assert "No From: name specified" in str(exc.value)

        args['from_name'] = 'Email Sender'

        with pytest.raises(CannotLoadConfiguration) as exc:
            m(**args)
        assert "No From: address specified" in str(exc.value)

        args['from_address'] = 'from@library.org'

        # With all the arguments specified, it works.
        emailer = m(**args)

        # If one of the templates can't be used, it doesn't work.
        args['templates']['key'] = EmailTemplate("%(nope)s", "email body")
        with pytest.raises(CannotLoadConfiguration) as exc:
            m(**args)
        assert r"Template '%(nope)s'/'email body' contains unrecognized key: KeyError('nope')" in str(exc.value)

    def test_templates(self):
        """Test the emails generated by the default templates."""
        integration = self._integration()
        emailer = Emailer.from_sitewide_integration(self._db)

        # Start with arguments common to both email templates.
        args = dict(
            rel_desc="support address",
            library="My Public Library",
            library_web_url="https://library/",
        )

        # Generate the address-designation template.
        designation_template = emailer.templates[Emailer.ADDRESS_DESIGNATED]
        body = designation_template.body(
            "me@registry", "you@library", **args
        )

        # Verify that the headers were set correctly.
        for phrase in [
            "From: me@registry",
            "To: you@library",
            "This address designated as the support address for My Public".replace(" ", "_"), # Part of the encoding process
        ]:
            assert phrase in body

        # Verify that the body was set correctly.
        expect = """This email address, you@library, has been registered with the Library Simplified library registry as the support address for the library My Public Library (https://library/).

If this is obviously wrong (for instance, you don't work at a public library), please accept our apologies and contact the Library Simplified support address at me@registry -- something has gone wrong.

If you do work at a public library, but you're not sure what this means, please speak to a technical point of contact at your library, or contact the Library Simplified support address at me@registry."""
        text_part = MIMEText(expect, 'plain', 'utf-8')
        assert text_part.get_payload() in body

        # The confirmation template has a couple extra fields that need
        # filling in.
        confirmation_template = emailer.templates[Emailer.ADDRESS_NEEDS_CONFIRMATION]
        args['confirmation_link'] = "http://registry/confirm"
        body2 = confirmation_template.body(
            "me@registry", "you@library", **args
        )

        # The address confirmation template is the address designation
        # template with a couple extra paragraphs and a different
        # subject line.
        extra = """If you do know what this means, you should also know that you
're not quite done. We need to confirm that you actually meant to use this email address for this purpose. If everything looks right, please visit this link:

http://registry/confirm

The link will expire in about a day. If the link expires, just re-register your library with the library registry, and a fresh confirmation email like this will be sent out."""

        # Verify the subject line
        assert "Confirm_the_" in body2

        # Verify that the extra content is there. (TODO: I wasn't able
        # to check the whole thing because expect2 parses into a
        # slightly different Message object than is generated by
        # Emailer.)
        expect2 = expect + "\n\n" + extra
        for phrase in [
                "\nhttp://registry/confirm\n",
                "The link will expire"
        ]:
            assert phrase in body2

    def test_send(self):
        """Validate our ability to construct and send email."""
        integration = self._integration()
        integration.setting("email1_subject").value = "subject %(arg)s"
        integration.setting("email1_body").value = "body %(arg)s"

        emailer = MockEmailer.from_sitewide_integration(self._db)
        emailer.templates['email1'] = EmailTemplate(
            "subject %(arg)s", "Hello, %(to_address)s, this is %(from_address)s."
        )
        mock_smtp = object()

        # Send an email using the template we just created.
        emailer.send("email1", "you@library", mock_smtp, arg="Value")

        # The template was filled out and passed into our mocked-up
        # _send_email implementation.
        (to, body, smtp) = emailer.emails.pop()
        assert to == "you@library"
        for phrase in [
            "From: Me <me@registry>",
            "To: you@library",
            "subject Value".replace(" ", "_"), # Part of the encoding process.
            "Hello, you@library, this is me@registry."
        ]:
            print(phrase)
            assert phrase in body
        assert smtp == mock_smtp

    def test__send_email(self):
        """Verify that send_email calls certain methods on smtplib.SMTP."""
        _ = self._integration()
        emailer = Emailer.from_sitewide_integration(self._db)
        mock_class = MockSMTP
        emailer._send_email("you@library", "email body", mock_class)

        # These smtplib.SMTP methods were called.
        init, connect, starttls, login, sendmail, quit = mock_class.calls
        assert init == ('__init__', (), {'host': emailer.smtp_host, 'port': emailer.smtp_port})
        assert connect == ('connect', (emailer.smtp_host, emailer.smtp_port), {})
        assert starttls == ('starttls', (), {})
        assert login == ('login', (emailer.smtp_username, emailer.smtp_password), {})
        assert sendmail == ('sendmail', (emailer.from_address, "you@library", "email body"), {})
        assert quit == ("quit", (), {})
