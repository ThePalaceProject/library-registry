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
from typing import Any

from palace.registry.config import (
    CannotLoadConfiguration,
    CannotSendEmail,
    EmailerNotConfigured,
)

# Set up an encoding/decoding between UTF-8 and quoted-printable.
# Otherwise, the bodies of email messages will be encoded with base64
# and they'll be hard to read. This way, only the non-ASCII characters
# need to be encoded.
charset.add_charset("utf-8", charset.QP, charset.QP, "utf-8")


@dataclass
class PendingEmail:
    """An email that has been requested but not yet sent.

    :param email_type: The name of the template to use.
    :param to_address: The addressee of the email. This is the address
        the email is about, even if delivery is redirected elsewhere.
    :param template_args: Arguments to use when filling out the template.
    """

    email_type: str
    to_address: str
    template_args: dict[str, Any] = field(default_factory=dict)


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
        "that would otherwise have been sent separately. Each section names its addressee."
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

    # Every key a notification template may reference. We use this to
    # catch templates that contain variables we won't be able to fill in.
    KNOWN_TEMPLATE_KEYS = [
        "rel_desc",
        "library",
        "library_web_url",
        "confirmation_link",
        "to_address",
        "from_address",
        "email",
        "registry_support",
    ]

    # The digest describes the whole message, so it only gets values that
    # apply to every section. Per-section values live in the sections.
    DIGEST_TEMPLATE_KEYS = [
        "library",
        "library_web_url",
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
        for email_type, template in self.templates.items():
            keys = (
                self.DIGEST_TEMPLATE_KEYS
                if email_type == self.DIGEST
                else self.KNOWN_TEMPLATE_KEYS
            )
            # The count is the one value that is a number at send time, so
            # a template may format it with %(count)d.
            test_template_values = {
                key: 1 if key == "count" else "value" for key in keys
            }
            try:
                template.render(**test_template_values)
            except Exception as e:
                m = f"Template '{template.subject_template}'/'{template.body_template}' contains unrecognized key: {e}"
                raise CannotLoadConfiguration(m)

            # A digest whose body does not render its sections would
            # silently drop every notification.
            if email_type == self.DIGEST:
                sentinel = "\x00sections\x00"
                rendered = template.text(
                    **{**test_template_values, "sections": sentinel}
                )
                if sentinel not in rendered:
                    raise CannotLoadConfiguration(
                        f"Template '{template.body_template}' for {self.DIGEST!r} must contain %(sections)s"
                    )

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

        Recipient addresses are compared without regard to case, since
        they almost always name the same mailbox. The first spelling seen
        is the one used in the To: header.

        Emails sent together are expected to concern the same library,
        since the digest subject names the library of the first email.

        Every recipient is attempted, even if an earlier one fails.

        :param pending: The emails to send.
        :param smtp_class: Use this class for the SMTP protocol client.
        :raise CannotSendEmail: If any email could not be rendered or
            accepted by the SMTP server. The exception names the
            addressees whose emails were not sent.
        """
        by_recipient: dict[str, tuple[str, list[PendingEmail]]] = {}
        for email in pending:
            recipient = self._effective_recipient(email.email_type, email.to_address)
            _, emails = by_recipient.setdefault(recipient.casefold(), (recipient, []))
            emails.append(email)

        from_header = f"{self.from_name} <{self.from_address}>"
        problems: list[str] = []
        unsent: list[PendingEmail] = []
        for recipient, emails in by_recipient.values():
            on_behalf_of = list(
                dict.fromkeys(
                    e.to_address
                    for e in emails
                    if e.to_address.casefold() != recipient.casefold()
                )
            )
            suffix = f" on behalf of {', '.join(on_behalf_of)}" if on_behalf_of else ""
            try:
                if len(emails) == 1:
                    [email] = emails
                    subject, text = self._render(email)
                    description = f"email of type {email.email_type!r}"
                else:
                    subject, text = self._render_digest(emails, recipient)
                    description = f"digest of {len(emails)} emails"
                self.log.info(f"Sending {description} to {recipient!r}{suffix}")
                body = build_message(from_header, recipient, subject, text)
                self._send_email(recipient, body, smtp_class)
            except Exception as exc:
                problem = f"Could not send email to {recipient!r}{suffix}: {exc!r}"
                self.log.error(problem, exc_info=exc)
                problems.append(problem)
                unsent.extend(emails)
        if unsent:
            raise CannotSendEmail(
                "; ".join(problems),
                addresses=list(dict.fromkeys(e.to_address for e in unsent)),
            )

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
        return template.render(**kwargs)

    def _render_digest(
        self, emails: list[PendingEmail], recipient: str
    ) -> tuple[str, str]:
        """Fill out the digest template for several emails bound for
        the same recipient.

        Each email is rendered as it would have been on its own, and the
        results become sections of the digest. The digest template itself
        is filled out only with values that describe the whole message,
        listed in DIGEST_TEMPLATE_KEYS. The library is taken from the
        first email, since emails sent together concern one library.

        :return: A 2-tuple (subject, text).
        """
        sections = []
        for email in emails:
            subject, text = self._render(email)
            sections.append(f"{subject}\n({email.to_address})\n\n{text}")
        # An Emailer built directly from a templates dict may lack a
        # digest template. The default one is always known to be valid.
        template = self.templates.get(self.DIGEST) or EmailTemplate(
            self.DEFAULT_DIGEST_SUBJECT, self.DEFAULT_DIGEST_TEMPLATE
        )
        first = emails[0].template_args
        return template.render(
            library=first.get("library"),
            library_web_url=first.get("library_web_url"),
            from_address=self.from_address,
            to_address=recipient,
            count=len(emails),
            sections=self.DIGEST_SECTION_DIVIDER.join(sections),
        )

    def _effective_recipient(self, email_type: str, default: str) -> str:
        """Override the recipient's email address, when applicable.

        The recipient override never applies to test emails.
        """
        if email_type == self.TEST:
            return default
        return self.recipient_address_override or default

    def _send_email(self, to_address, body, smtp_class=SMTP):
        """Actually send an email."""
        # We let the constructor connect and don't call connect() ourselves,
        # because a second connect would abandon the first socket, and only
        # the constructor records the host name that starttls() needs.
        smtp = smtp_class(host=self.smtp_host, port=self.smtp_port)
        try:
            smtp.starttls()
            smtp.login(self.smtp_username, self.smtp_password)
            smtp.sendmail(self.from_address, to_address, body)
            try:
                smtp.quit()
            except Exception as exc:
                # The server already accepted the message.
                self.log.warning(
                    f"SMTP session did not close cleanly after sending to {to_address!r}",
                    exc_info=exc,
                )
        finally:
            # Whatever happened, do not leave the socket open.
            smtp.close()

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
            raise EmailerNotConfigured("No email integration is configured.")

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

    def render(self, **kwargs) -> tuple[str, str]:
        """Fill out both templates.

        :return: A 2-tuple (subject, text).
        """
        return self.subject(**kwargs), self.text(**kwargs)


def build_message(from_header: str, to_header: str, subject: str, text: str) -> str:
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
