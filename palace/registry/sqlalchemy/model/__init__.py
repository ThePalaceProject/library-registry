"""SQLAlchemy models for Library Registry."""

# Base setup and utilities
# Model classes
from palace.registry.sqlalchemy.constants import LibraryType
from palace.registry.sqlalchemy.model.admin import Admin
from palace.registry.sqlalchemy.model.audience import Audience, libraries_audiences
from palace.registry.sqlalchemy.model.base import Base
from palace.registry.sqlalchemy.model.collection_summary import CollectionSummary
from palace.registry.sqlalchemy.model.configuration_setting import ConfigurationSetting
from palace.registry.sqlalchemy.model.delegated_patron_identifier import (
    DelegatedPatronIdentifier,
    ShortClientTokenDecoder,
)
from palace.registry.sqlalchemy.model.external_integration import ExternalIntegration
from palace.registry.sqlalchemy.model.hyperlink import Hyperlink
from palace.registry.sqlalchemy.model.library import Library, LibraryAlias
from palace.registry.sqlalchemy.model.place import Place, PlaceAlias
from palace.registry.sqlalchemy.model.resource import Resource, Validation
from palace.registry.sqlalchemy.model.service_area import ServiceArea

__all__ = [
    # Base and utilities
    "Base",
    # Models
    "Admin",
    "Audience",
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
