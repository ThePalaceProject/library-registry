import email
import smtplib

from config import (
    CannotLoadConfiguration,
)


class Emailer(object):
    """A class for sending small amounts of email."""

    # Goal and setting names for the ExternalIntegration.
    GOAL = 'email'
    PORT = 'port'
    FROM_ADDRESS = 'from_address'
    FROM_NAME = 'from_name'

    DEFAULT_FROM_NAME = 'Library Simplified registry support'

    # Constants for different types of email.
    ADDRESS_DESIGNATED = 'address_designated'
    ADDRESS_NEEDS_CONFIRMATION = 'address_registered'

    EMAIL_TYPES = [ADDRESS_DESIGNATED, ADDRESS_NEEDS_CONFIRMATION]

    DEFAULT_ADDRESS_DESIGNATED_SUBJECT = "This address designated as the %(rel_desc)s for %(library)s"
    DEFAULT_ADDRESS_NEEDS_CONFIRMATION_SUBJECT = "Confirm the %(rel_desc)s for %(library)s"

    DEFAULT_ADDRESS_DESIGNATED_TEMPLATE = """This email address, %(to_address)s, has been registered with the Library Simplified library registry as the %(rel_desc)s for the library %(library)s (%(library_web_url)s).

If this is obviously wrong (for instance, you don't work at a public library), please accept our apologies and contact the Library Simplified support address at %(from_address)s -- something has gone wrong.

If you do work at a public library, but you're not sure what this means, please speak to a technical point of contact at your library, or contact the Library Simplified support address at %(from_address)s."""

    NEEDS_CONFIRMATION_ADDITION = """If you do know what this means, you should also know that you're not quite done. We need to confirm that you actually meant to use this email address for this purpose. If everything looks right, please visit this link:

%(confirmation_link)s

The link will expire in about a day. If the link expires, just re-register your library with the library registry, and a fresh confirmation email like this will be sent out."""

    BODIES = {
        ADDRESS_DESIGNATED : DEFAULT_ADDRESS_DESIGNATED_TEMPLATE,
        ADDRESS_NEEDS_CONFIRMATION : DEFAULT_ADDRESS_DESIGNATED_TEMPLATE + "\n\n" + NEEDS_CONFIRMATION_ADDITION
    }

    SUBJECTS = {
        ADDRESS_DESIGNATED: DEFAULT_ADDRESS_DESIGNATED_SUBJECT,
        ADDRESS_NEEDS_CONFIRMATION : DEFAULT_ADDRESS_NEEDS_CONFIRMATION_SUBJECT,
    }

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
                integration.setting(email_type + "_subject").value or
                cls.SUBJECTS[email_type]
            )
            body = (
                integration.setting(email_type + "_body").value or
                cls.BODIES[email_type]
            )
            email_templates[email_type] = EmailTemplate(subject, body)

        return cls(smtp_username=integration.username,
                   smtp_password=integration.password,
                   smtp_host=host, smtp_port=port, from_name=from_name,
                   from_address=from_address,
                   templates=email_templates)

    @classmethod
    def _sitewide_integration(cls, _db):
        """Find the ExternalIntegration for the emailer."""
        from model import ExternalIntegration
        qu = _db.query(ExternalIntegration).filter(
            ExternalIntegration.goal==cls.GOAL
        )
        integrations = qu.all()
        if not integrations:
            raise CannotLoadConfiguration(
                "No email integration is configured."
            )
            return None

        if len(integrations) > 1:
            # If there are multiple integrations configured, none of
            # them can be the 'site-wide' configuration.
            raise CannotLoadConfiguration(
                'Multiple email integrations are configured'
            )

        [integration] = integrations
        return integration

    def __init__(self, smtp_username, smtp_password, smtp_host, smtp_port,
                 from_name, from_address, templates):
        """Constructor."""
        if not smtp_username:
            raise CannotLoadConfiguration("No SMTP username specified")
        self.smtp_username = smtp_username
        if not smtp_password:
            raise CannotLoadConfiguration("No SMTP password specified")
        self.smtp_password = smtp_password
        if not smtp_host:
            raise CannotLoadConfiguration("No SMTP host specified")
        self.smtp_host = smtp_host
        if not smtp_port:
            raise CannotLoadConfiguration("No SMTP port specified")
        self.smtp_port = smtp_port
        if not from_name:
            raise CannotLoadConfiguration("No From: name specified")
        if not from_address:
            raise CannotLoadConfiguration("No From: address specified")
        self.from_name = from_name
        self.from_address = from_address
        self.templates = templates

    def send(self, email_type, to_address, smtp=None, **kwargs):
        """Generate an email from a template and send it.

        :param email_type: The name of the template to use.
        :param to_address: Addressee of the email.
        :param smtp: Use this object as a mock instead of creating an
            smtplib.SMTP object.
        :param kwargs: Arguments to use when generating the email from
            a template.
        """
        if not email_type in self.templates:
            raise ValueError("No such email template: %s" % email_type)
        template = self.templates[email_type]
        from_header = '%s <%s>' % (self.from_name, self.from_address)
        body = template.body(from_header, to_address, **kwargs)
        return self._send_email(to_address, body, smtp)

    def _send_email(self, to_address, body, smtp=None):
        """Actually send an email."""
        smtp = smtp or smtplib.SMTP()
        smtp.connect(self.smtp_host, self.smtp_port)
        smtp.login(self.smtp_username, self.smtp_password)
        smtp.sendmail(self.from_address, to_address, body)
        smtp.quit()


class EmailTemplate(object):
    """A template for email messages."""

    def __init__(self, subject_template, body_template):
        self.subject_template = subject_template
        self.body_template = body_template

    def body(self, from_address, to_address, **kwargs):
        """Generate the complete body of the email message, including headers.

        :param from_address: Originating address.
        :param to_address: Destination address.
        :param kwargs: Arguments to use when filling out the template.
        """

        message = email.Message.Message()
        message['From'] = from_address
        message['To'] = to_address
        message['Subject'] = self.subject_template % kwargs
        kwargs['to_address'] = to_address
        kwargs['from_address'] = from_address
        message.set_payload(self.body_template % kwargs)
        return message.as_string()
