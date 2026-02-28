"""Audience model for library audience types."""

from __future__ import annotations

from flask_babel import lazy_gettext as _
from sqlalchemy import Column, ForeignKey, Integer, Table, Unicode, UniqueConstraint
from sqlalchemy.orm import relationship

from .base import Base, get_one_or_create


class Audience(Base):
    """A class of person served by a library."""

    __tablename__ = "audiences"

    # The general public
    PUBLIC = "public"

    # Pre-university students
    EDUCATIONAL_PRIMARY = "educational-primary"

    # University students
    EDUCATIONAL_SECONDARY = "educational-secondary"

    # Academics and researchers
    RESEARCH = "research"

    # People with print disabilities
    PRINT_DISABILITY = "print-disability"

    # A catch-all for other specialized audiences.
    OTHER = "other"

    KNOWN_AUDIENCES = [
        PUBLIC,
        EDUCATIONAL_PRIMARY,
        EDUCATIONAL_SECONDARY,
        RESEARCH,
        PRINT_DISABILITY,
        OTHER,
    ]

    id = Column(Integer, primary_key=True)
    name = Column(Unicode, index=True, unique=True)

    libraries = relationship(
        "Library", secondary="libraries_audiences", back_populates="audiences"
    )

    @classmethod
    def lookup(cls, _db, name):
        if name not in cls.KNOWN_AUDIENCES:
            raise ValueError(_("Unknown audience: %(name)s", name=name))
        audience, is_new = get_one_or_create(_db, Audience, name=name)
        return audience


# Join table for many-to-many relationship between libraries and audiences
libraries_audiences = Table(
    "libraries_audiences",
    Base.metadata,
    Column(
        "library_id", Integer, ForeignKey("libraries.id"), index=True, nullable=False
    ),
    Column(
        "audience_id", Integer, ForeignKey("audiences.id"), index=True, nullable=False
    ),
    UniqueConstraint("library_id", "audience_id"),
)
