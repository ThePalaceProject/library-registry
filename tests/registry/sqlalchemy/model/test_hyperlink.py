import datetime

from palace.registry.config import Configuration
from palace.registry.emailer import Emailer
from palace.registry.sqlalchemy.model.configuration_setting import ConfigurationSetting
from palace.registry.sqlalchemy.model.hyperlink import Hyperlink
from palace.registry.util.datetime_helpers import utc_now
from tests.fixtures.database import DatabaseTransactionFixture


class TestHyperlink:
    def test_build_notification(self, db: DatabaseTransactionFixture):
        """`build_notification` builds, but does not send, the email that tells
        the target of a hyperlink about it, and starts or restarts the
        validation of the target when needed.
        """
        url_for_calls = []

        def url_for(controller, **kwargs):
            """Just a convenient place to mock Flask's url_for()."""
            url_for_calls.append((controller, kwargs))
            return "http://url/"

        ConfigurationSetting.sitewide(
            db.session, Configuration.REGISTRY_CONTACT_EMAIL
        ).value = "me@registry"

        library = db.library()
        library.web_url = "http://library/"

        # A hyperlink to something other than an email address
        # produces no notification and starts no validation.
        web_link, _ = library.set_hyperlink(Hyperlink.HELP_REL, "http://help.library/")
        assert web_link.email_address is None
        assert web_link.build_notification(url_for) is None
        assert web_link.resource.validation is None

        # A hyperlink to a new email address produces a notification
        # asking for confirmation, and starts the validation process.
        link, _ = library.set_hyperlink(
            Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL, "mailto:you@library"
        )
        assert link.email_address == "you@library"
        email = link.build_notification(url_for)
        assert email.email_type == Emailer.ADDRESS_NEEDS_CONFIRMATION
        assert email.to_address == "you@library"
        validation = link.resource.validation
        secret = validation.secret

        # These arguments were created to fill in the
        # ADDRESS_NEEDS_CONFIRMATION template.
        kwargs = email.template_args
        assert kwargs["registry_support"] == "me@registry"
        assert kwargs["email"] == "you@library"
        assert kwargs["rel_desc"] == "copyright designated agent"
        assert kwargs["library"] == library.name
        assert kwargs["library_web_url"] == library.web_url
        assert kwargs["confirmation_link"] == "http://url/"

        # url_for was called to create the confirmation link.
        assert url_for_calls.pop() == (
            "confirm_resource",
            dict(resource_id=link.resource.id, secret=secret),
        )

        # If a Resource we already know about is associated with a new
        # Hyperlink, the notification only announces the new role.
        link2, _ = library.set_hyperlink(Hyperlink.HELP_REL, "mailto:you@library")
        email2 = link2.build_notification(url_for)
        assert email2.email_type == Emailer.ADDRESS_DESIGNATED
        assert email2.to_address == "you@library"
        assert email2.template_args["rel_desc"] == "patron help contact address"

        # url_for was not called again, since an ADDRESS_DESIGNATED
        # email does not include a validation link.
        assert url_for_calls == []

        # And the Validation was not reset.
        assert link.resource.validation.secret == secret

        # The same goes for the third role a library can assign.
        link3, _ = library.set_hyperlink(
            Hyperlink.INTEGRATION_CONTACT_REL, "mailto:you@library"
        )
        email3 = link3.build_notification(url_for)
        assert email3.email_type == Emailer.ADDRESS_DESIGNATED
        assert email3.template_args["rel_desc"] == "integration point of contact"
        assert url_for_calls == []

        # Same if we somehow build another notification for a Hyperlink
        # with an active Validation.
        assert link.build_notification(url_for).email_type == Emailer.ADDRESS_DESIGNATED
        assert link.resource.validation.secret == secret
        assert url_for_calls == []

        # However, if a Hyperlink's Validation has expired, it is reset
        # and a new ADDRESS_NEEDS_CONFIRMATION notification is built.
        now = utc_now()
        validation.started_at = now - datetime.timedelta(days=10)
        email4 = link.build_notification(url_for)
        assert email4.email_type == Emailer.ADDRESS_NEEDS_CONFIRMATION
        assert email4.template_args["confirmation_link"] == "http://url/"

        # The Validation has been reset, and the confirmation link was
        # built from the new secret.
        assert link.resource.validation == validation
        assert validation.deadline > now
        assert secret != validation.secret
        assert url_for_calls.pop() == (
            "confirm_resource",
            dict(resource_id=link.resource.id, secret=validation.secret),
        )
