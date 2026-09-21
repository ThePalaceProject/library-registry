"""Hyperlink model for library links."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKey, Integer, Unicode, UniqueConstraint
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm.session import Session

from palace.registry.sqlalchemy.model.base import Base
from palace.registry.sqlalchemy.util import create, get_one_or_create

if TYPE_CHECKING:
    from palace.registry.emailer import PendingEmail


class Hyperlink(Base):
    """A link between a Library and a Resource.

    We trust that the Resource is actually associated with the Library
    because the library told us about it; either directly, during
    registration, or by putting a link in its Authentication For OPDS
    document.
    """

    INTEGRATION_CONTACT_REL = "http://librarysimplified.org/rel/integration-contact"
    COPYRIGHT_DESIGNATED_AGENT_REL = (
        "http://librarysimplified.org/rel/designated-agent/copyright"
    )
    HELP_REL = "help"

    # Descriptions of the link relations, used in emails.
    REL_DESCRIPTIONS = {
        INTEGRATION_CONTACT_REL: "integration point of contact",
        COPYRIGHT_DESIGNATED_AGENT_REL: "copyright designated agent",
        HELP_REL: "patron help contact address",
    }

    # Hyperlinks with these relations are not for public consumption.
    PRIVATE_RELS = [INTEGRATION_CONTACT_REL]

    __tablename__ = "hyperlinks"

    id = Column(Integer, primary_key=True)
    rel = Column(Unicode, index=True, nullable=False)
    library_id = Column(Integer, ForeignKey("libraries.id"), index=True)
    resource_id = Column(Integer, ForeignKey("resources.id"), index=True)

    # A Library can have multiple links with the same rel, but we only
    # need to keep track of one.
    __table_args__ = (UniqueConstraint("library_id", "rel"),)

    @hybrid_property
    def href(self):
        if not self.resource:
            return None
        return self.resource.href

    @href.setter
    def href(self, url):
        from palace.registry.sqlalchemy.model.resource import Resource

        _db = Session.object_session(self)
        resource, is_new = get_one_or_create(_db, Resource, href=url)
        self.resource = resource

    @property
    def email_address(self) -> str | None:
        """The email address this hyperlink points to, without any
        `mailto:` prefix, or None if it does not point to one.
        """
        href = self.href
        if not href:
            return None
        if href.startswith("mailto:"):
            href = href[7:]
        return href if re.match(r"[^@]+@.+", href) else None

    def build_notification(self, url_for: Callable[..., str]) -> PendingEmail | None:
        """Build, but do not send, the email that notifies the target of
        this hyperlink that it is, in fact, a target of the hyperlink.

        If the underlying resource needs a new validation, the validation
        is restarted here and the email is an ADDRESS_NEEDS_CONFIRMATION,
        asking the person on the other end to confirm the address.
        Otherwise it is an ADDRESS_DESIGNATED, informing the person on the
        other end that their (probably already validated) email address
        was associated with another library.

        :param url_for: An implementation of Flask's url_for, used to
            generate a validation link if necessary.
        :return: A PendingEmail, or None if there is nothing to send.
        """
        from palace.registry.config import Configuration
        from palace.registry.emailer import Emailer, PendingEmail
        from palace.registry.sqlalchemy.model.configuration_setting import (
            ConfigurationSetting,
        )

        _db = Session.object_session(self)

        # These shouldn't happen, but just to be safe, do nothing if
        # this Hyperlink is disconnected from the other data model
        # objects it needs to do its job.
        resource = self.resource
        library = self.library
        if not resource or not library:
            return None

        # Default to sending an informative email with no validation
        # link.
        email_type = Emailer.ADDRESS_DESIGNATED
        to_address = self.email_address
        if not to_address:
            return None

        # Make sure there's a Validation object associated with this
        # Resource.
        if resource.validation is None:
            from palace.registry.sqlalchemy.model.resource import Validation

            resource.validation, is_new = create(_db, Validation)
        else:
            is_new = False
        validation = resource.validation

        if is_new or not validation.active:
            # Either this Validation was just created or it expired
            # before being verified. Restart the validation process
            # and send an email that includes a validation link.
            validation.restart()
            email_type = Emailer.ADDRESS_NEEDS_CONFIRMATION

        # Create values for all the variables expected by the default
        # templates.
        template_args = dict(
            rel_desc=Hyperlink.REL_DESCRIPTIONS.get(self.rel, self.rel),
            library=library.name,
            library_web_url=library.web_url,
            email=to_address,
            registry_support=ConfigurationSetting.sitewide(
                _db, Configuration.REGISTRY_CONTACT_EMAIL
            ).value,
        )
        if email_type == Emailer.ADDRESS_NEEDS_CONFIRMATION:
            template_args["confirmation_link"] = url_for(
                "confirm_resource", resource_id=resource.id, secret=validation.secret
            )
        return PendingEmail(email_type, to_address, template_args)
