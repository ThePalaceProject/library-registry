import email as email_lib
import quopri
from contextlib import nullcontext
from email.header import decode_header
from email.mime.text import MIMEText
from unittest import mock

import pytest

from palace.registry.config import CannotLoadConfiguration, CannotSendEmail
from palace.registry.emailer import Emailer, EmailTemplate, PendingEmail, build_message
from tests.fixtures.database import DatabaseTransactionFixture


def render(template: EmailTemplate, from_header: str, to_header: str, **kwargs) -> str:
    """Compose a template's subject and text into a complete message for
    inspection, with the headers standing in for missing address arguments.
    """
    kwargs.setdefault("from_address", from_header)
    kwargs.setdefault("to_address", to_header)
    return build_message(from_header, to_header, *template.render(**kwargs))


class TestEmailTemplate:
    """Test the ability to generate email messages."""

    def test_body(self):
        template = EmailTemplate(
            "A %(color)s subject", "The subject is %(color)s but the body is %(number)d"
        )
        body = render(
            template, "me@example.com", "you@example.com", color="red", number=22
        )

        # We always generate a MIME multipart message because
        # that's how we handle non-ASCII characters.
        for expect in (
            "Content-Type: multipart/mixed;",
            "Content-Transfer-Encoding: quoted-printable",
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
            "\n\nThe subject is red but the body is 22",
        ):
            assert expect in body

    def test_unicode_quoted_printable(self):
        # Create an email message that includes Unicode characters in
        # its subject and body.
        snowman = "\N{SNOWMAN}"
        template = EmailTemplate(
            "A snowman for you! %s" % snowman, "Here he is: %s" % snowman
        )
        body = render(template, "me@example.com", "you@example.com")
        # The SNOWMAN character is encoded as quoted-printable in both
        # the subject and the message contents.
        quoted_printable_snowman = quopri.encodestring(snowman.encode("utf8")).decode(
            "utf8"
        )
        for template in (
            "Subject: =?utf-8?q?A_snowman_for_you!_%(snowman)s?=",
            "\n\nHere he is: %(snowman)s",
        ):
            expect = template % dict(snowman=quoted_printable_snowman)
            assert expect in body


class MockEmailer(Emailer):
    """Store outgoing emails in a list."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.emails = []

    def _send_email(self, to_address, body, smtp):
        self.emails.append((to_address, body, smtp))


class MockBrokenEmailer(Emailer):
    """Raise a generic Exception when _send_email() is called"""

    def _send_email(*args):
        raise Exception("message from MockBrokenEmailer")


class TestEmailer:
    def _emailer(
        self,
        db: DatabaseTransactionFixture,
        monkeypatch: pytest.MonkeyPatch,
        override: str | None = None,
    ) -> Emailer:
        """Build an Emailer from a sitewide integration, with the
        recipient override set to `override` if given.
        """
        if override:
            monkeypatch.setenv(Emailer.ENV_RECIPIENT_OVERRIDE_ADDRESS, override)
        self._integration(db)
        return Emailer.from_sitewide_integration(db.session)

    def _integration(self, db: DatabaseTransactionFixture):
        """Configure a complete sitewide email integration."""
        integration = db.external_integration("my protocol")
        integration.goal = Emailer.GOAL
        integration.username = "smtp_username"
        integration.password = "smtp_password"
        integration.url = "smtp_host"
        integration.setting(Emailer.PORT).value = "234"
        integration.setting(Emailer.FROM_NAME).value = "Me"
        integration.setting(Emailer.FROM_ADDRESS).value = "me@registry"
        return integration

    def test__sitewide_integration(self, db: DatabaseTransactionFixture):
        """Test the ability to find a sitewide integration for sending out
        email.
        """
        m = Emailer._sitewide_integration
        # If there's no integration with goal=Emailer.GOAL,
        # _sitewide_integration raises an exception.
        with pytest.raises(CannotLoadConfiguration) as exc:
            m(db.session)
        assert "No email integration is configured" in str(exc.value)

        # If there's only one, _sitewide_integration finds it.
        integration = self._integration(db)
        assert m(db.session) == integration

        # If there are multiple integrations with goal=Emailer.GOAL, no
        # sitewide configuration can be determined.
        self._integration(db)
        with pytest.raises(CannotLoadConfiguration) as exc:
            m(db.session)
        assert "Multiple email integrations are configured" in str(exc.value)

    def test_from_sitewide_integration(self, db: DatabaseTransactionFixture):
        """Test the ability to load an Emailer from a sitewide integration."""
        integration = self._integration(db)
        emailer = Emailer.from_sitewide_integration(db.session)

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
        # known email types, and build another Emailer. The digest body
        # must keep its sections.
        def custom_body(email_type: str) -> str:
            sections = " %(sections)s" if email_type == Emailer.DIGEST else ""
            return f"body {email_type}{sections}"

        for email_type in Emailer.EMAIL_TYPES:
            integration.setting(email_type + "_subject").value = (
                "subject %s" % email_type
            )
            integration.setting(email_type + "_body").value = custom_body(email_type)
        emailer = Emailer.from_sitewide_integration(db.session)
        for email_type in Emailer.EMAIL_TYPES:
            template = emailer.templates[email_type]
            assert template.subject_template == "subject %s" % email_type
            assert template.body_template == custom_body(email_type)

    def test_constructor(self):
        """Verify the exceptions raised when required constructor
        arguments are missing.
        """
        args = {
            x: None
            for x in (
                "smtp_username",
                "smtp_password",
                "smtp_host",
                "smtp_port",
                "from_name",
                "from_address",
            )
        }
        args["templates"] = {}

        m = Emailer
        with pytest.raises(CannotLoadConfiguration) as exc:
            m(**args)
        assert "Emailer instantiated with missing params" in str(exc.value)
        assert "smtp_username" in str(exc.value)
        assert "smtp_password" in str(exc.value)
        assert "smtp_host" in str(exc.value)
        assert "smtp_port" in str(exc.value)
        assert "from_name" in str(exc.value)
        assert "from_address" in str(exc.value)

        args["smtp_username"] = "user"
        args["smtp_password"] = "password"
        args["smtp_host"] = "host"
        args["smtp_port"] = "port"
        args["from_name"] = "Email Sender"
        args["from_address"] = "from@library.org"

        # With all the arguments specified, it works.
        m(**args)

        # Every value the notification code supplies may be used, including
        # the two that the default templates do not.
        args["templates"]["key"] = EmailTemplate(
            "%(email)s", "Contact %(registry_support)s."
        )
        m(**args)
        del args["templates"]["key"]

        # The count is a number at send time, so a template may format
        # it as one.
        args["templates"][Emailer.DIGEST] = EmailTemplate(
            "%(count)d notifications", "%(sections)s"
        )
        m(**args)

        # If one of the templates can't be used, it doesn't work.
        args["templates"]["key"] = EmailTemplate("%(nope)s", "email body")
        with pytest.raises(CannotLoadConfiguration) as exc:
            m(**args)
        assert (
            r"Template '%(nope)s'/'email body' contains unrecognized key: 'nope'"
            in str(exc.value)
        )
        del args["templates"]["key"]

        # An unknown key in the body is caught as well as one in the subject.
        args["templates"]["key"] = EmailTemplate("subject", "%(nope)s")
        with pytest.raises(CannotLoadConfiguration) as exc:
            m(**args)
        assert "contains unrecognized key: 'nope'" in str(exc.value)
        del args["templates"]["key"]

        # A digest template that leaves out the sections would silently
        # drop every notification, so it is rejected too.
        args["templates"][Emailer.DIGEST] = EmailTemplate("subject", "%(count)s notes")
        with pytest.raises(CannotLoadConfiguration) as exc:
            m(**args)
        assert "must contain %(sections)s" in str(exc.value)

    def test_templates(self, db: DatabaseTransactionFixture):
        """Test the emails generated by the default templates."""
        self._integration(db)
        emailer = Emailer.from_sitewide_integration(db.session)

        # Start with arguments common to all the email templates.
        args = dict(
            rel_desc="support address",
            library="My Public Library",
            library_web_url="https://library/",
        )

        # Generate the address-designation template.
        designation_template = emailer.templates[Emailer.ADDRESS_DESIGNATED]
        body = render(designation_template, "me@registry", "you@library", **args)

        # Verify that the headers were set correctly.
        for phrase in [
            "From: me@registry",
            "To: you@library",
            "This address designated as the support address for My Public".replace(
                " ", "_"
            ),  # Part of encoding
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
        text_part = MIMEText(expect, "plain", "utf-8")
        assert text_part.get_payload() in body

        # The confirmation template has a couple extra fields that need
        # filling in.
        confirmation_template = emailer.templates[Emailer.ADDRESS_NEEDS_CONFIRMATION]
        args["confirmation_link"] = "http://registry/confirm"
        body2 = render(confirmation_template, "me@registry", "you@library", **args)

        # Verify the subject line
        assert "Confirm_the_" in body2

        # Verify that the extra content is there. (TODO: I wasn't able
        # to check the whole thing because expect2 parses into a
        # slightly different Message object than is generated by
        # Emailer.)
        for phrase in ["\nhttp://registry/confirm\n", "The link will expire"]:
            assert phrase in body2

    def test_send(self, db: DatabaseTransactionFixture):
        """Validate our ability to construct and send email."""
        self._integration(db)
        emailer = MockEmailer.from_sitewide_integration(db.session)
        emailer.templates["email1"] = EmailTemplate(
            "subject %(arg)s", "Hello, %(to_address)s, this is %(from_address)s."
        )
        mock_smtp = object()

        # Send an email using the template we just created.
        emailer.send("email1", "you@library", mock_smtp, arg="Value")

        # The template was filled out and passed into our mocked-up
        # _send_email implementation.
        to, body, smtp = emailer.emails.pop()
        assert to == "you@library"
        for phrase in [
            "From: Me <me@registry>",
            "To: you@library",
            "subject Value".replace(" ", "_"),  # Part of the encoding process.
            "Hello, you@library, this is me@registry.",
        ]:
            assert phrase in body
        assert smtp == mock_smtp

    @pytest.mark.parametrize(
        "email_type, override_is_specified, expected_recipient",
        [
            pytest.param(Emailer.TEST, True, "default@example.org", id="test-override"),
            pytest.param(
                Emailer.TEST, False, "default@example.org", id="test-no-override"
            ),
            pytest.param(
                Emailer.ADDRESS_DESIGNATED,
                True,
                "override@example.org",
                id="designated-override",
            ),
            pytest.param(
                Emailer.ADDRESS_DESIGNATED,
                False,
                "default@example.org",
                id="designated-no-override",
            ),
            pytest.param(
                Emailer.ADDRESS_NEEDS_CONFIRMATION,
                True,
                "override@example.org",
                id="confirmation-override",
            ),
            pytest.param(
                Emailer.ADDRESS_NEEDS_CONFIRMATION,
                False,
                "default@example.org",
                id="confirmation-no-override",
            ),
        ],
    )
    def test_override_recipient(
        self,
        email_type: str,
        override_is_specified: bool,
        expected_recipient,
        db: DatabaseTransactionFixture,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Except for test email, recipient should be overridden when an override is specified in the environment."""

        default_recipient = "default@example.org"
        override_recipient = "override@example.org"
        emailer = self._emailer(
            db, monkeypatch, override_recipient if override_is_specified else None
        )

        # Setup a dummy template.
        emailer.templates[email_type] = EmailTemplate("Email", "This is an email.")

        # Send the email and ensure that we used the correct recipient.
        with mock.patch.object(Emailer, "_send_email", autospec=True) as send_email:
            emailer.send(email_type, default_recipient)
            send_email.assert_called_once()
            assert expected_recipient == send_email.call_args_list[0][0][1]

    @staticmethod
    def _text(body: str) -> str:
        """Extract the decoded plain text from a complete email message."""
        [part] = email_lib.message_from_string(body).get_payload()
        return part.get_payload(decode=True).decode("utf-8")

    @pytest.mark.parametrize(
        "override, addresses, expected",
        [
            pytest.param(
                None,
                ["a@library", "b@library"],
                {"a@library": ["a@library"], "b@library": ["b@library"]},
                id="different-recipients-no-override",
            ),
            pytest.param(
                None,
                ["a@library", "a@library"],
                {"a@library": ["a@library", "a@library"]},
                id="same-recipient-no-override",
            ),
            pytest.param(
                "override@example.org",
                ["a@library", "b@library"],
                {"override@example.org": ["a@library", "b@library"]},
                id="different-recipients-with-override",
            ),
            pytest.param(
                "override@example.org",
                ["a@library"],
                {"override@example.org": ["a@library"]},
                id="single-email-with-override",
            ),
            pytest.param(
                None,
                ["Help@Library", "help@library"],
                {"Help@Library": ["Help@Library", "help@library"]},
                id="same-recipient-different-case",
            ),
            pytest.param(None, [], {}, id="nothing-to-send"),
        ],
    )
    def test_send_all(
        self,
        override: str | None,
        addresses: list[str],
        expected: dict[str, list[str]],
        db: DatabaseTransactionFixture,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Emails bound for the same recipient, whether because they share
        an addressee or because of the recipient override, are combined
        into one digest. Others are sent separately.

        `expected` maps each recipient to the addressees whose emails
        should have been delivered to it.
        """
        emailer = self._emailer(db, monkeypatch, override)
        pending = [
            PendingEmail(
                Emailer.ADDRESS_NEEDS_CONFIRMATION,
                address,
                dict(
                    rel_desc=f"role {i}",
                    library="My Library",
                    library_web_url="https://library/",
                    confirmation_link=f"https://registry/confirm/{i}",
                ),
            )
            for i, address in enumerate(addresses)
        ]
        with mock.patch.object(Emailer, "_send_email", autospec=True) as send_email:
            emailer.send_all(pending)

        assert send_email.call_count == len(expected)
        sent = {
            call.args[1]: self._text(call.args[2]) for call in send_email.call_args_list
        }
        assert list(sent) == list(expected)
        # The To: header always names the effective recipient.
        for call in send_email.call_args_list:
            assert email_lib.message_from_string(call.args[2])["To"] == call.args[1]
        for recipient, addressees in expected.items():
            text = sent[recipient]
            emails = [e for e in pending if e.to_address in addressees]
            for email in emails:
                # Every addressee and every confirmation link survives,
                # so the recipient can act on each one.
                assert email.to_address in text
                assert email.template_args["confirmation_link"] in text
                assert email.template_args["rel_desc"] in text
            if len(emails) > 1:
                assert f"combines {len(emails)} notifications" in text
                for email in emails:
                    assert f"\n({email.to_address})\n\n" in text
                assert text.count(Emailer.DIGEST_SECTION_DIVIDER.strip()) == (
                    len(emails) - 1
                )
            else:
                assert "combines" not in text

    def test_send_all_digest_headers(
        self, db: DatabaseTransactionFixture, monkeypatch: pytest.MonkeyPatch
    ):
        """The digest carries its own subject and is addressed to the
        effective recipient, while each section keeps the original subject.
        """
        emailer = self._emailer(db, monkeypatch, "override@example.org")
        args = dict(library="My Library", library_web_url="https://library/")
        pending = [
            PendingEmail(
                Emailer.ADDRESS_NEEDS_CONFIRMATION,
                "a@library",
                dict(args, rel_desc="help address", confirmation_link="https://c/1"),
            ),
            PendingEmail(
                Emailer.ADDRESS_DESIGNATED,
                "b@library",
                dict(args, rel_desc="copyright agent"),
            ),
        ]
        with mock.patch.object(Emailer, "_send_email", autospec=True) as send_email:
            emailer.send_all(pending)

        [call] = send_email.call_args_list
        message = email_lib.message_from_string(call.args[2])
        assert message["To"] == "override@example.org"
        [(subject, _)] = decode_header(message["Subject"])
        assert subject.decode("utf-8") == "2 notifications for My Library"

        text = self._text(call.args[2])
        # Each section opens with the original subject and the addressee
        # on whose behalf it is sent.
        assert "Confirm the help address for My Library\n(a@library)\n\n" in text
        assert (
            "This address designated as the copyright agent for My Library"
            "\n(b@library)\n\n"
        ) in text
        # The sections appear in the order the emails were requested.
        assert text.index("a@library") < text.index("b@library")

    def test_send_all_without_digest_template(self, db: DatabaseTransactionFixture):
        """An Emailer built without a digest template still combines
        emails to one recipient, using the default digest template.
        """
        self._integration(db)
        emailer = Emailer.from_sitewide_integration(db.session)
        del emailer.templates[Emailer.DIGEST]

        args = dict(rel_desc="help", library="L", library_web_url="https://l/")
        pending = [
            PendingEmail(Emailer.ADDRESS_DESIGNATED, "a@library", args),
            PendingEmail(Emailer.ADDRESS_DESIGNATED, "a@library", args),
        ]
        with mock.patch.object(Emailer, "_send_email", autospec=True) as send_email:
            emailer.send_all(pending)

        [call] = send_email.call_args_list
        assert "combines 2 notifications" in self._text(call.args[2])

    def test_send_all_test_email_is_never_overridden(
        self, db: DatabaseTransactionFixture, monkeypatch: pytest.MonkeyPatch
    ):
        """A test email keeps its own recipient even when the override is
        set, so it is not combined with the overridden emails.
        """
        emailer = self._emailer(db, monkeypatch, "override@example.org")
        emailer.templates[Emailer.TEST] = EmailTemplate("Test", "This is a test.")

        pending = [
            PendingEmail(Emailer.TEST, "tester@example.org"),
            PendingEmail(
                Emailer.ADDRESS_DESIGNATED,
                "a@library",
                dict(rel_desc="help", library="L", library_web_url="https://l/"),
            ),
        ]
        with mock.patch.object(Emailer, "_send_email", autospec=True) as send_email:
            emailer.send_all(pending)
        assert [c.args[1] for c in send_email.call_args_list] == [
            "tester@example.org",
            "override@example.org",
        ]

    @pytest.mark.parametrize(
        "failing_step, override, fail_on, attempted, unsent",
        [
            pytest.param(
                "_send_email",
                None,
                {"b@library"},
                ["a@library", "b@library", "c@library"],
                ["b@library"],
                id="smtp-failure",
            ),
            pytest.param(
                "_render",
                None,
                {"b@library"},
                ["a@library", "c@library"],
                ["b@library"],
                id="template-failure",
            ),
            pytest.param(
                "_send_email",
                None,
                {"a@library", "c@library"},
                ["a@library", "b@library", "c@library"],
                ["a@library", "c@library"],
                id="two-groups-fail",
            ),
            pytest.param(
                "_send_email",
                "override@example.org",
                {"override@example.org"},
                ["override@example.org"],
                ["a@library", "b@library", "c@library"],
                id="digest-smtp-failure",
            ),
            pytest.param(
                "_render",
                "override@example.org",
                {"b@library"},
                [],
                ["a@library", "b@library", "c@library"],
                id="digest-template-failure",
            ),
        ],
    )
    def test_send_all_partial_failure(
        self,
        failing_step: str,
        override: str | None,
        fail_on: set[str],
        attempted: list[str],
        unsent: list[str],
        db: DatabaseTransactionFixture,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A failure while rendering or sending the email for one
        recipient does not stop the others. Afterward, CannotSendEmail
        names the addressees whose emails were not sent, never the
        override address. A rendering failure stops before the SMTP
        send is attempted.

        `fail_on` holds the addresses at which `failing_step` fails: the
        addressee for a rendering failure, the SMTP recipient for a send
        failure. `attempted` lists the recipients handed to the SMTP send.
        """
        emailer = self._emailer(db, monkeypatch, override)
        args = dict(rel_desc="help", library="L", library_web_url="https://l/")
        pending = [
            PendingEmail(Emailer.ADDRESS_DESIGNATED, address, args)
            for address in ("a@library", "b@library", "c@library")
        ]

        original_render = Emailer._render

        def flaky_render(self, email):
            if email.to_address in fail_on:
                raise KeyError("boom")
            return original_render(self, email)

        def flaky_send(self, to_address, body, smtp_class):
            if to_address in fail_on:
                raise Exception("boom")

        render_patch = (
            mock.patch.object(Emailer, "_render", flaky_render)
            if failing_step == "_render"
            else nullcontext()
        )
        with mock.patch.object(Emailer, "_send_email", autospec=True) as send_email:
            if failing_step == "_send_email":
                send_email.side_effect = flaky_send
            with render_patch, pytest.raises(CannotSendEmail) as exc:
                emailer.send_all(pending)

        assert exc.value.addresses == unsent
        assert [call.args[1] for call in send_email.call_args_list] == attempted

        # One problem is reported per failed recipient, each with the
        # original error.
        failed_groups = 1 if override else len(unsent)
        problems = str(exc.value).split("; ")
        assert len(problems) == failed_groups
        assert all("boom" in problem for problem in problems)

    def test_send_all_failure_names_each_addressee_once(
        self, db: DatabaseTransactionFixture, monkeypatch: pytest.MonkeyPatch
    ):
        """When several emails to one addressee cannot be sent, the
        addressee is reported once.
        """
        emailer = self._emailer(db, monkeypatch)
        args = dict(rel_desc="help", library="L", library_web_url="https://l/")
        pending = [
            PendingEmail(Emailer.ADDRESS_DESIGNATED, "a@library", args),
            PendingEmail(Emailer.ADDRESS_DESIGNATED, "a@library", args),
        ]
        with (
            mock.patch.object(Emailer, "_send_email", side_effect=Exception("boom")),
            pytest.raises(CannotSendEmail) as exc,
        ):
            emailer.send_all(pending)
        assert exc.value.addresses == ["a@library"]

    def test_send_failure(self, db: DatabaseTransactionFixture):
        """
        GIVEN: An Emailer whose _send_email method raises an Exception
        WHEN:  send_all() catches that exception
        THEN:  A more specific exception naming the addressee should be raised
        """
        self._integration(db)
        emailer = MockBrokenEmailer.from_sitewide_integration(db.session)
        emailer.templates["some_email"] = EmailTemplate("subject", "Hello.")
        with pytest.raises(CannotSendEmail) as exc:
            emailer.send("some_email", "me@domain.tld")
        assert exc.value.addresses == ["me@domain.tld"]
        assert "message from MockBrokenEmailer" in str(exc.value)

    def test_send_unknown_email_type(self, db: DatabaseTransactionFixture):
        """An email type with no template cannot be sent, and the failure
        names the addressee like any other.
        """
        self._integration(db)
        emailer = Emailer.from_sitewide_integration(db.session)
        with pytest.raises(CannotSendEmail) as exc:
            emailer.send("no_such_type", "me@domain.tld")
        assert exc.value.addresses == ["me@domain.tld"]
        assert "No such email template" in str(exc.value)

    @mock.patch("smtplib.SMTP", autospec=True)
    def test__send_email2(self, mock_class, db: DatabaseTransactionFixture):
        """Verify that send_email calls certain methods on smtplib.SMTP."""

        _ = self._integration(db)
        emailer = Emailer.from_sitewide_integration(db.session)
        email_recipient = "you@library"
        email_body = "email body"

        expected_calls = [
            mock.call(host=emailer.smtp_host, port=emailer.smtp_port),
            mock.call().connect(emailer.smtp_host, emailer.smtp_port),
            mock.call().starttls(),
            mock.call().login(emailer.smtp_username, emailer.smtp_password),
            mock.call().sendmail(emailer.from_address, email_recipient, email_body),
            mock.call().quit(),
        ]

        emailer._send_email(email_recipient, email_body, mock_class)

        mock_class.assert_has_calls(expected_calls, any_order=False)

    @mock.patch("smtplib.SMTP", autospec=True)
    def test__send_email_quit_failure(self, mock_class, db: DatabaseTransactionFixture):
        """Once the server has accepted the message, a failure while
        closing the session is not a send failure.
        """
        self._integration(db)
        emailer = Emailer.from_sitewide_integration(db.session)
        mock_class.return_value.quit.side_effect = Exception("connection lost")
        emailer._send_email("you@library", "email body", mock_class)
        mock_class.return_value.sendmail.assert_called_once()
        # The socket is still closed.
        mock_class.return_value.close.assert_called_once()
