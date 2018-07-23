from nose.tools import (
    assert_raises_regexp,
    eq_,
    set_trace,
)
from . import DatabaseTest

from config import CannotLoadConfiguration
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


class MockSMTP(object):
    """Mock of smtplib.SMTP that records all incoming calls."""

    calls = []

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
        assert_raises_regexp(
            CannotLoadConfiguration,
            'No email integration is configured',
            m, self._db
        )

        # If there's only one, _sitewide_integration finds it.
        integration = self._integration()
        eq_(integration, m(self._db))

        # If there are multiple integrations with goal=Emailer.GOAL, no
        # sitewide configuration can be determined.
        duplicate = self._integration()
        assert_raises_regexp(
            CannotLoadConfiguration,
            'Multiple email integrations are configured',
            m, self._db
        )

    def test_from_sitewide_integration(self):
        """Test the ability to load an Emailer from a sitewide integration."""
        integration = self._integration()
        emailer = Emailer.from_sitewide_integration(self._db)

        # The Emailer's configuration is based on the sitewide integration.
        eq_("smtp_username", emailer.smtp_username)
        eq_("smtp_password", emailer.smtp_password)
        eq_("smtp_host", emailer.smtp_host)
        eq_("me@registry", emailer.from_address)

        # Default EmailTemplates have been created for all known email types.
        for email_type in Emailer.EMAIL_TYPES:
            template = emailer.templates[email_type]
            eq_(Emailer.SUBJECTS[email_type], template.subject_template)
            eq_(Emailer.BODIES[email_type], template.body_template)

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
            eq_("subject %s" % email_type, template.subject_template)
            eq_("body %s" % email_type, template.body_template)

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
        assert_raises_regexp(
            CannotLoadConfiguration, "No SMTP username specified", m, **args
        )
        args['smtp_username'] = 'user'

        assert_raises_regexp(
            CannotLoadConfiguration, "No SMTP password specified", m, **args
        )
        args['smtp_password'] = 'password'

        assert_raises_regexp(
            CannotLoadConfiguration, "No SMTP host specified", m, **args
        )
        args['smtp_host'] = 'host'

        assert_raises_regexp(
            CannotLoadConfiguration, "No SMTP port specified", m, **args
        )
        args['smtp_port'] = 'port'

        assert_raises_regexp(
            CannotLoadConfiguration, "No From: name specified", m, **args
        )
        args['from_name'] = 'Email Sender'

        assert_raises_regexp(
            CannotLoadConfiguration, "No From: address specified", m, **args
        )
        args['from_address'] = 'from@library.org'

        # With all the arguments specified, it works.
        emailer = m(**args)

        # If one of the templates can't be used, it doesn't work.
        args['templates']['key'] = EmailTemplate("%(nope)s", "email body")
        assert_raises_regexp(
            CannotLoadConfiguration,
            "Template '%\(nope\)s'/'email body' contains unrecognized key",
            m, **args
        )

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
        eq_(body, """From: me@registry
To: you@library
Subject: This address designated as the support address for My Public Library

This email address, you@library, has been registered with the Library Simplified library registry as the support address for the library My Public Library (https://library/).

If this is obviously wrong (for instance, you don't work at a public library), please accept our apologies and contact the Library Simplified support address at me@registry -- something has gone wrong.

If you do work at a public library, but you're not sure what this means, please speak to a technical point of contact at your library, or contact the Library Simplified support address at me@registry.""")

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
        extra = """If you do know what this means, you should also know that you're not quite done. We need to confirm that you actually meant to use this email address for this purpose. If everything looks right, please visit this link:

http://registry/confirm

The link will expire in about a day. If the link expires, just re-register your library with the library registry, and a fresh confirmation email like this will be sent out."""
        new_body = body.replace("Subject: This address designated as the ",
                                "Subject: Confirm the ")
        eq_(body2, new_body+"\n\n"+extra)

    def test_send(self):
        """Validate our ability to construct and send email."""
        integration = self._integration()
        integration.setting("email1_subject").value = "subject %(arg)s"
        integration.setting("email1_body").value = "body %(arg)s"

        emailer = MockEmailer.from_sitewide_integration(self._db)
        emailer.templates['email1'] = EmailTemplate(
            "subject %(arg)s", "Hello, %(from_address)s."
        )
        mock_smtp = object()

        # Send an email using the template we just created.
        emailer.send("email1", "you@library", mock_smtp, arg="Value")

        # The template was filled out and passed into our mocked-up
        # _send_email implementation.
        (to, body, smtp) = emailer.emails.pop()
        eq_("you@library", to)
        eq_("""From: Me <me@registry>
To: you@library
Subject: subject Value

Hello, me@registry.""", body)
        eq_(mock_smtp, smtp)

    def test__send_email(self):
        """Verify that send_email calls certain methods on smtplib.SMTP."""
        integration = self._integration()
        emailer = Emailer.from_sitewide_integration(self._db)
        mock = MockSMTP()
        emailer._send_email("you@library", "email body", mock)

        # Five smtplib.SMTP methods were called.
        connect, starttls, login, sendmail, quit = mock.calls
        eq_(
            ('connect', (emailer.smtp_host, emailer.smtp_port), {}),
            connect
        )

        eq_(
            ('starttls', (), {}),
            starttls
        )

        eq_(
            ('login', (emailer.smtp_username, emailer.smtp_password), {}),
            login
        )

        eq_(
            ('sendmail', (emailer.from_address, "you@library", "email body"), {}),
            sendmail
        )

        eq_(("quit", (), {}), quit)
