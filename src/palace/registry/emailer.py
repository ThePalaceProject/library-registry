from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from email import charset
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from smtplib import SMTP

from palace.registry.config import CannotLoadConfiguration, CannotSendEmail

# Set up an encoding/decoding between UTF-8 and quoted-printable.
# Otherwise, the bodies of email messages will be encoded with base64
# and they'll be hard to read. This way, only the non-ASCII characters
# need to be encoded.
charset.add_charset("utf-8", charset.QP, charset.QP, "utf-8")


@dataclass(frozen=True)
class PendingEmail:
    """An email that has been requested but not yet sent.

    :param email_type: The name of the template to use.
    :param to_address: The addressee of the email. This is the address
        the email is about, even if delivery is redirected elsewhere.
    :param template_args: Arguments to use when filling out the template.
    """

    email_type: str
    to_address: str
    template_args: dict = field(default_factory=dict)


class Emailer:
    """A class for sending small amounts of email."""

    log = logging.getLogger("Emailer")

    # Goal and setting names for the ExternalIntegration.
    GOAL = "email"
    PORT = "port"
    FROM_ADDRESS = "from_address"
    FROM_NAME = "from_name"
    DEFAULT_FROM_NAME = "Library Simplified registry support"

    ENV_RECIPIENT_OVERRIDE_ADDRESS = "EMAILER_RECIPIENT_OVERRIDE"

    # Constants for different types of email.
    ADDRESS_DESIGNATED = "address_designated"
    ADDRESS_NEEDS_CONFIRMATION = "address_needs_confirmation"
    DIGEST = "digest"

    # The test email type is never redirected by the recipient override.
    TEST = "test"

    EMAIL_TYPES = [ADDRESS_DESIGNATED, ADDRESS_NEEDS_CONFIRMATION, DIGEST]

    DEFAULT_ADDRESS_DESIGNATED_SUBJECT = (
        "This address designated as the %(rel_desc)s for %(library)s"
    )
    DEFAULT_ADDRESS_NEEDS_CONFIRMATION_SUBJECT = (
        "Confirm the %(rel_desc)s for %(library)s"
    )
    DEFAULT_DIGEST_SUBJECT = "%(count)s notifications for %(library)s"

    DEFAULT_ADDRESS_DESIGNATED_TEMPLATE = (
        "This email address, %(to_address)s, has been registered with the Library Simplified library registry "
        "as the %(rel_desc)s for the library %(library)s (%(library_web_url)s)."
        "\n\n"
        "If this is obviously wrong (for instance, you don't work at a public library), please accept our "
        "apologies and contact the Library Simplified support address at %(from_address)s -- something has gone wrong."
        "\n\n"
        "If you do work at a public library, but you're not sure what this means, please speak to a technical point "
        "of contact at your library, or contact the Library Simplified support address at %(from_address)s."
    )

    NEEDS_CONFIRMATION_ADDITION = (
        "If you do know what this means, you should also know that you're not quite done. We need to confirm that "
        "you actually meant to use this email address for this purpose. If everything looks right, please "
        "visit this link:"
        "\n\n"
        "%(confirmation_link)s"
        "\n\n"
        "The link will expire in about a day. If the link expires, just re-register your library with the library "
        "registry, and a fresh confirmation email like this will be sent out."
    )

    # Several emails bound for the same address are combined into one
    # digest email. Each original email becomes one section of the digest.
    DEFAULT_DIGEST_TEMPLATE = (
        "This message combines %(count)s notifications from the Library Simplified library registry "
        "that would otherwise have been sent separately to this address."
        "\n\n"
        "%(sections)s"
    )
    DIGEST_SECTION_DIVIDER = "\n\n" + "-" * 40 + "\n\n"

    BODIES = {
        ADDRESS_DESIGNATED: DEFAULT_ADDRESS_DESIGNATED_TEMPLATE,
        ADDRESS_NEEDS_CONFIRMATION: DEFAULT_ADDRESS_DESIGNATED_TEMPLATE
        + "\n\n"
        + NEEDS_CONFIRMATION_ADDITION,
        DIGEST: DEFAULT_DIGEST_TEMPLATE,
    }

    SUBJECTS = {
        ADDRESS_DESIGNATED: DEFAULT_ADDRESS_DESIGNATED_SUBJECT,
        ADDRESS_NEEDS_CONFIRMATION: DEFAULT_ADDRESS_NEEDS_CONFIRMATION_SUBJECT,
        DIGEST: DEFAULT_DIGEST_SUBJECT,
    }

    # Every key a template may reference. We use this to catch templates
    # that contain variables we won't be able to fill in.
    KNOWN_TEMPLATE_KEYS = [
        "rel_desc",
        "library",
        "library_web_url",
        "confirmation_link",
        "to_address",
        "from_address",
        "count",
        "sections",
    ]

    def __init__(
        self,
        smtp_username,
        smtp_password,
        smtp_host,
        smtp_port,
        from_name,
        from_address,
        templates,
    ):
        config_errors = []
        required_parameters = (
            "smtp_username",
            "smtp_password",
            "smtp_host",
            "smtp_port",
            "from_name",
            "from_address",
        )
        for param_name in required_parameters:
            if not locals()[param_name]:
                config_errors.append(param_name)

        if config_errors:
            msg = "Emailer instantiated with missing params: " + ", ".join(
                config_errors
            )
            raise CannotLoadConfiguration(msg)

        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.from_name = from_name
        self.from_address = from_address
        self.templates = templates

        self.recipient_address_override = os.environ.get(
            self.ENV_RECIPIENT_OVERRIDE_ADDRESS, None
        )

        # Make sure the templates don't contain any template values we can't handle.
        test_template_values = {key: "value" for key in self.KNOWN_TEMPLATE_KEYS}
        for template in list(self.templates.values()):
            try:
                template.body("from address", "to address", **test_template_values)
            except Exception as e:
                m = f"Template '{template.subject_template}'/'{template.body_template}' contains unrecognized key: {e}"
                raise CannotLoadConfiguration(m)

    def send(
        self,
        email_type: str,
        to_address: str,
        smtp_class: type[SMTP] = SMTP,
        **kwargs,
    ) -> None:
        """Generate an email from a template and send it.

        :param email_type: The name of the template to use.
        :param to_address: Addressee of the email.
        :param smtp_class: Use this class for the SMTP protocol client.
        :param kwargs: Arguments to use when generating the email from
            a template.
        """
        self.send_all([PendingEmail(email_type, to_address, kwargs)], smtp_class)

    def send_all(
        self, pending: Iterable[PendingEmail], smtp_class: type[SMTP] = SMTP
    ) -> None:
        """Generate emails from templates and send them, combining any
        that are bound for the same recipient into a single digest email.

        Emails end up at the same recipient either because their
        addressees are the same, or because the recipient override
        redirects them all to one address. In either case, the original
        `to_address` of each email is kept in the message body, so it is
        clear on whose behalf each part of the email is being sent.

        Emails sent together are expected to concern the same library,
        since the digest subject names the library of the first email.

        :param pending: The emails to send.
        :param smtp_class: Use this class for the SMTP protocol client.
        :raise CannotSendEmail: If any email cannot be rendered or sent.
            Emails to earlier recipients may already have been sent.
        """
        by_recipient: dict[str, list[PendingEmail]] = {}
        for email in pending:
            recipient = self._effective_recipient(email.email_type, email.to_address)
            by_recipient.setdefault(recipient, []).append(email)

        from_header = f"{self.from_name} <{self.from_address}>"
        for recipient, emails in by_recipient.items():
            on_behalf_of = [e.to_address for e in emails if e.to_address != recipient]
            suffix = f" on behalf of {on_behalf_of!r}" if on_behalf_of else ""
            try:
                if len(emails) == 1:
                    [email] = emails
                    subject, text = self._render(email)
                    description = f"email of type {email.email_type!r}"
                else:
                    subject, text = self._render_digest(emails, recipient)
                    description = f"digest of {len(emails)} emails"
                self.log.info(f"Sending {description} to {recipient!r}{suffix}")
                body = EmailTemplate.message(from_header, recipient, subject, text)
                self._send_email(recipient, body, smtp_class)
            except Exception as exc:
                raise CannotSendEmail(
                    f"Could not send email to {recipient!r}{suffix}: {exc}"
                ) from exc

    def _template(self, email_type: str) -> EmailTemplate:
        if email_type not in self.templates:
            raise ValueError("No such email template: %s" % email_type)
        return self.templates[email_type]

    def _render(self, email: PendingEmail) -> tuple[str, str]:
        """Fill out the template for one email.

        :return: A 2-tuple (subject, text).
        """
        template = self._template(email.email_type)
        kwargs = dict(
            email.template_args,
            from_address=self.from_address,
            to_address=email.to_address,
        )
        return template.subject(**kwargs), template.text(**kwargs)

    def _render_digest(
        self, emails: list[PendingEmail], recipient: str
    ) -> tuple[str, str]:
        """Fill out the digest template for several emails bound for
        the same recipient.

        Each email is rendered as it would have been on its own, and the
        results become sections of the digest. The digest template itself
        is filled out with the template arguments of the first email,
        plus `count`, `sections`, and the `to_address` of the digest.

        :return: A 2-tuple (subject, text).
        """
        sections = []
        for email in emails:
            subject, text = self._render(email)
            sections.append(f"{subject}\n\n{text}")
        template = self._template(self.DIGEST)
        kwargs = dict(
            emails[0].template_args,
            from_address=self.from_address,
            to_address=recipient,
            count=len(emails),
            sections=self.DIGEST_SECTION_DIVIDER.join(sections),
        )
        return template.subject(**kwargs), template.text(**kwargs)

    def _effective_recipient(self, email_type: str, default: str) -> str:
        """Override the recipient's email address, when applicable.

        The recipient override never applies to test emails.
        """
        if email_type == self.TEST:
            return default
        return self.recipient_address_override or default

    def _send_email(self, to_address, body, smtp_class=SMTP):
        """Actually send an email."""
        smtp = smtp_class(host=self.smtp_host, port=self.smtp_port)
        smtp.connect(self.smtp_host, self.smtp_port)
        smtp.starttls()
        smtp.login(self.smtp_username, self.smtp_password)
        smtp.sendmail(self.from_address, to_address, body)
        smtp.quit()

    @classmethod
    def from_sitewide_integration(cls, _db):
        """Create an Emailer from a site-wide email integration.

        :param _db: A database connection
        """
        integration = cls._sitewide_integration(_db)
        host = integration.url
        port = integration.setting(cls.PORT).int_value or 587
        from_address = integration.setting(cls.FROM_ADDRESS).value
        from_name = integration.setting(cls.FROM_NAME).value or cls.DEFAULT_FROM_NAME

        email_templates = {}
        for email_type in cls.EMAIL_TYPES:
            subject = (
                integration.setting(email_type + "_subject").value
                or cls.SUBJECTS[email_type]
            )
            body = (
                integration.setting(email_type + "_body").value
                or cls.BODIES[email_type]
            )
            template = EmailTemplate(subject, body)
            email_templates[email_type] = template

        return cls(
            smtp_username=integration.username,
            smtp_password=integration.password,
            smtp_host=host,
            smtp_port=port,
            from_name=from_name,
            from_address=from_address,
            templates=email_templates,
        )

    @classmethod
    def _sitewide_integration(cls, _db):
        """Find the ExternalIntegration for the emailer."""
        from palace.registry.sqlalchemy.model.external_integration import (
            ExternalIntegration,
        )

        qu = _db.query(ExternalIntegration).filter(ExternalIntegration.goal == cls.GOAL)
        integrations = qu.all()
        if not integrations:
            raise CannotLoadConfiguration("No email integration is configured.")
            return None

        if len(integrations) > 1:
            # If there are multiple integrations configured, none of
            # them can be the 'site-wide' configuration.
            raise CannotLoadConfiguration("Multiple email integrations are configured")

        [integration] = integrations
        return integration


class EmailTemplate:
    """A template for email messages."""

    def __init__(self, subject_template, body_template):
        self.subject_template = subject_template
        self.body_template = body_template

    def subject(self, **kwargs) -> str:
        """Fill out the subject template."""
        return self.subject_template % kwargs

    def text(self, **kwargs) -> str:
        """Fill out the body template."""
        return self.body_template % kwargs

    @staticmethod
    def message(from_header: str, to_header: str, subject: str, text: str) -> str:
        """Assemble a complete email message, including headers.

        :param from_header: Originating address to use in From: header.
        :param to_header: Destination address to use in To: header.
        :param subject: The subject line.
        :param text: The plain text body.
        """
        message = MIMEMultipart("mixed")
        message["From"] = from_header
        message["To"] = to_header
        message["Subject"] = Header(subject, "utf-8")
        message.attach(MIMEText(text, "plain", "utf-8"))
        return message.as_string()

    def body(self, from_header, to_header, **kwargs):
        """
        Generate the complete body of the email message, including headers.

        :param from_header: Originating address to use in From: header.
        :param to_header: Destination address to use in To: header.
        :param kwargs: Arguments to use when filling out the template.
        """
        # This might look ugly, because %(from_address)s in a template
        # is expected to be an unadorned email address, whereas this
        # might look like '"Name" <email>', but it's better than
        # nothing.
        for k, v in (("to_address", to_header), ("from_address", from_header)):
            if k not in kwargs:
                kwargs[k] = v

        return self.message(
            from_header, to_header, self.subject(**kwargs), self.text(**kwargs)
        )
