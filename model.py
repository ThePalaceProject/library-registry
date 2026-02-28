"""Model module - imports from palace.registry.sqlalchemy.model for backward compatibility."""

from __future__ import annotations

# Import everything from the new package location
from palace.registry.sqlalchemy.model import (
    Admin,
    Audience,
    Base,
    CollectionSummary,
    ConfigurationSetting,
    DelegatedPatronIdentifier,
    ExternalIntegration,
    Hyperlink,
    Library,
    LibraryAlias,
    LibraryType,
    Place,
    PlaceAlias,
    Resource,
    ServiceArea,
    ShortClientTokenDecoder,
    Validation,
    libraries_audiences,
)

__all__ = [
    "Admin",
    "Audience",
    "Base",
    "CollectionSummary",
    "ConfigurationSetting",
    "DelegatedPatronIdentifier",
    "ExternalIntegration",
    "Hyperlink",
    "Library",
    "LibraryAlias",
    "LibraryType",
    "Place",
    "PlaceAlias",
    "Resource",
    "ServiceArea",
    "ShortClientTokenDecoder",
    "Validation",
    "libraries_audiences",
]
