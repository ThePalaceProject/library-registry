"""SQLAlchemy models for Library Registry."""

# Base setup and utilities
# Model classes
from palace.registry.sqlalchemy.constants import LibraryType

from .admin import Admin
from .audience import Audience, libraries_audiences
from .base import (
    Base,
)
from .collection_summary import CollectionSummary
from .configuration_setting import ConfigurationSetting
from .delegated_patron_identifier import (
    DelegatedPatronIdentifier,
    ShortClientTokenDecoder,
)
from .external_integration import ExternalIntegration
from .hyperlink import Hyperlink
from .library import Library, LibraryAlias
from .place import Place, PlaceAlias
from .resource import Resource, Validation
from .service_area import ServiceArea

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
