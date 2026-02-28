"""ServiceArea model for geographic service areas."""

from __future__ import annotations

from sqlalchemy import Column, Enum, ForeignKey, Integer, UniqueConstraint

from .base import Base


class ServiceArea(Base):
    """Designates a geographic area served by a Library.

    A ServiceArea maps a Library to a Place. People living in this
    Place have service from the Library.
    """

    __tablename__ = "serviceareas"

    id = Column(Integer, primary_key=True)
    library_id = Column(Integer, ForeignKey("libraries.id"), index=True)

    place_id = Column(Integer, ForeignKey("places.id"), index=True)

    # A library may have a ServiceArea because people in that area are
    # eligible for service, or because the library specifically
    # focuses on that area.
    ELIGIBILITY = "eligibility"
    FOCUS = "focus"
    servicearea_type_enum = Enum(ELIGIBILITY, FOCUS, name="servicearea_type")
    type = Column(
        servicearea_type_enum, index=True, nullable=False, default=ELIGIBILITY
    )

    __table_args__ = (UniqueConstraint("library_id", "place_id", "type"),)
