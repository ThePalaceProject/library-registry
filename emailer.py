from config import (
    CannotLoadConfiguration,
)
from model import (
    ExternalIntegration
)

class Email(object):
    def __init__(self, from_address, subject, template):
        self.from_address = from_address
        self.subject = subject
        self.template = template

    def body(self, to_address, **kwargs):
        # Add to, from, and subject headers
        return self.template % kwargs


class Emailer(object):
    """A class for sending small amounts of email."""

    # Constants for different types of email.
    VALIDATION = 'validation'

    REQUIRED_TYPES = [VALIDATION]

    @classmethod
    def from_sitewide_integration(cls, _db, url_for):
        """Create an Emailer from a site-wide email integration."""
        integration = cls.sitewide_integration(_db)
        integration.username
        integration.password
        host = integration.url
        port = integration.setting(self.PORT).value or 25
        validation_subject = integration.setting(self.VALIDATION_SUBJECT).value
        validation_template = integration.setting(self.VALIDATION_TEMPLATE).value
        from_address = integration.setting(self.FROM_ADDRESS).value

        if not validation_subject or not validation_template or not from_address:
            raise CannotLoadConfiguration("Email configuration is incomplete")

        templates = { VALIDATION : Email(template, subject)}

        return cls(user=integration.username, password=integration.password,
                   host=host, port=port, from_address=from_address,
                   templates=templates, url_for=url_for)

    @classmethod
    def sitewide_integration(cls, _db):
        """Find the ExternalIntegration for the emailer."""
        from model import ExternalIntegration
        qu = _db.query(ExternalIntegration).filter(
            ExternalIntegration.goal==ExternalIntegration.EMAIL_GOAL
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

    def __init__(self, user, password, host, port, from_address, templates,
                 url_for):
        self.user = user
        self.password = password
        self.host = host
        self.port = port
        self.from_address = from_address
        self.templates = templates
        self.url_for = url_for

        for i in self.REQUIRED_TEMPLATES:
            if not i in self.templates:
                raise CannotLoadConfiguration(
                    _("Missing required template type %s") % i
                )

    def send_validation_email(self, validation):
        """Construct and send an email that can validate the given Validation
        object.
        """
        url = self.url_for("validate", secret=validation.secret)
        email = self.templates[self.VALIDATION]
        to_address = validation.resource.href
        body = email.body(to_address, address=to_address, url=url)

        return self._send_email(to_address, body)

    def _send_email(self, to_address, body):
        smtp = smtplib.SMTP_SSL(self.host, self.port)
        smtp.sendmail(self.from_address, to_address, body)
        smtp.quit()
