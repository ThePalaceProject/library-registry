"""Resource and Validation models for link validation."""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Unicode
from sqlalchemy.orm import backref, relationship
from sqlalchemy.orm.session import Session

from palace.registry.sqlalchemy.model.base import Base
from palace.registry.sqlalchemy.util import create, generate_secret
from palace.registry.util.datetime_helpers import utc_now


class Validation(Base):
    """An attempt (successful, in-progress, or failed) to validate a
    Resource.
    """

    __tablename__ = "validations"

    EXPIRES_AFTER = datetime.timedelta(days=1)

    id = Column(Integer, primary_key=True)
    success = Column(Boolean, index=True, default=False)
    started_at = Column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
        default=utc_now(),
    )

    # Used in OPDS catalogs to convey the status of a validation attempt.
    STATUS_PROPERTY = "https://schema.org/reservationStatus"

    # These constants are used in OPDS catalogs as values of
    # schema:reservationStatus.
    CONFIRMED = "https://schema.org/ReservationConfirmed"
    IN_PROGRESS = "https://schema.org/ReservationPending"
    INACTIVE = "https://schema.org/ReservationCancelled"

    resource = relationship(
        "Resource", backref=backref("validation", uselist=False), uselist=False
    )

    # The only way to validate a Resource is to prove you know the
    # corresponding secret.
    secret = Column(Unicode, default=generate_secret, unique=True)

    def restart(self):
        """Start a new validation attempt, cancelling any previous attempt.

        This does not send out a validation email -- that needs to be
        handled separately by something capable of generating the URL
        to the validation controller.
        """
        self.started_at = utc_now()
        self.secret = generate_secret()
        self.success = False

    @property
    def deadline(self):
        if self.success:
            return None
        return self.started_at + self.EXPIRES_AFTER

    @property
    def active(self):
        """Is this Validation still active?

        An inactive Validation can't be marked as successful -- it
        needs to be reset.
        """
        now = utc_now()
        return not self.success and now < self.deadline

    def mark_as_successful(self):
        """Register the fact that the validation attempt has succeeded."""
        if self.success:
            raise Exception("This validation has already succeeded.")
        if not self.active:
            raise Exception("This validation has expired.")
        self.secret = None
        self.success = True

        # TODO: This may cause one or more libraries to switch from
        # "not completely validated" to "completely validated".


class Resource(Base):
    """A URI, potentially linked to multiple libraries, or to a single
    library through multiple relationships.

    e.g. a library consortium may use a single email address as the
    patron help address and the integration contact address for all of
    its libraries. That address only needs to be validated once.
    """

    __tablename__ = "resources"

    id = Column(Integer, primary_key=True)
    href = Column(Unicode, nullable=False, index=True, unique=True)
    hyperlinks = relationship("Hyperlink", backref="resource")

    # Every Resource may have at most one Validation. There's no
    # need to validate it separately for every relationship.
    validation_id = Column(Integer, ForeignKey("validations.id"), index=True)

    def restart_validation(self):
        """Start or restart the validation process for this resource."""
        if not self.validation:
            _db = Session.object_session(self)
            self.validation, ignore = create(_db, Validation)
        self.validation.restart()
        return self.validation
