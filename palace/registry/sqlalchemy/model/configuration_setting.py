"""ConfigurationSetting model for configuration management."""

from __future__ import annotations

import json

from sqlalchemy import Column, ForeignKey, Integer, Unicode, UniqueConstraint
from sqlalchemy.orm.session import Session

from ..util import generate_secret, get_one, get_one_or_create
from .base import Base


class ConfigurationSetting(Base):
    """An extra piece of site configuration.

    A ConfigurationSetting may be associated with an
    ExternalIntegration, a Library, both, or neither.

    * The secret used by the circulation manager to sign OAuth bearer
      tokens is not associated with an ExternalIntegration or with a
      Library.

    * The link to a library's privacy policy is associated with the
      Library, but not with any particular ExternalIntegration.

    * The "website ID" for an Overdrive collection is associated with
      an ExternalIntegration (the Overdrive integration), but not with
      any particular Library (since multiple libraries might share an
      Overdrive collection).

    * The "identifier prefix" used to determine which library a patron
      is a patron of, is associated with both a Library and an
      ExternalIntegration.
    """

    __tablename__ = "configurationsettings"
    id = Column(Integer, primary_key=True)
    external_integration_id = Column(
        Integer, ForeignKey("externalintegrations.id"), index=True
    )
    library_id = Column(Integer, ForeignKey("libraries.id"), index=True)
    key = Column(Unicode, index=True)
    _value = Column(Unicode, name="value")

    __table_args__ = (UniqueConstraint("external_integration_id", "library_id", "key"),)

    def __repr__(self):
        return "<ConfigurationSetting: key=%s, ID=%d>" % (self.key, self.id)

    @classmethod
    def sitewide_secret(cls, _db, key):
        """Find or create a sitewide shared secret.

        The value of this setting doesn't matter, only that it's
        unique across the site and that it's always available.
        """
        secret = ConfigurationSetting.sitewide(_db, key)
        if not secret.value:
            secret.value = generate_secret()
            # Commit to get this in the database ASAP.
            _db.commit()
        return secret.value

    @classmethod
    def explain(cls, _db, include_secrets=False):
        """Explain all site-wide ConfigurationSettings."""
        lines = []
        site_wide_settings = []

        for setting in (
            _db.query(ConfigurationSetting)
            .filter(ConfigurationSetting.library_id == None)
            .filter(ConfigurationSetting.external_integration == None)
        ):
            if not include_secrets and setting.key.endswith("_secret"):
                continue
            site_wide_settings.append(setting)
        if site_wide_settings:
            lines.append("Site-wide configuration settings:")
            lines.append("---------------------------------")
        for setting in sorted(site_wide_settings, key=lambda s: s.key):
            lines.append(f"{setting.key}='{setting.value}'")
        return lines

    @classmethod
    def sitewide(cls, _db, key):
        """Find or create a sitewide ConfigurationSetting."""
        return cls.for_library_and_externalintegration(_db, key, None, None)

    @classmethod
    def for_library(cls, key, library):
        """Find or create a ConfigurationSetting for the given Library."""
        _db = Session.object_session(library)
        return cls.for_library_and_externalintegration(_db, key, library, None)

    @classmethod
    def for_externalintegration(cls, key, externalintegration):
        """Find or create a ConfigurationSetting for the given
        ExternalIntegration.
        """
        _db = Session.object_session(externalintegration)
        return cls.for_library_and_externalintegration(
            _db, key, None, externalintegration
        )

    @classmethod
    def for_library_and_externalintegration(
        cls, _db, key, library, external_integration
    ):
        """Find or create a ConfigurationSetting associated with a Library
        and an ExternalIntegration.
        """
        library_id = None
        if library:
            library_id = library.id
        setting, ignore = get_one_or_create(
            _db,
            ConfigurationSetting,
            library_id=library_id,
            external_integration=external_integration,
            key=key,
        )
        return setting

    @property
    def library(self):
        from .library import Library

        _db = Session.object_session(self)
        if self.library_id:
            return get_one(_db, Library, id=self.library_id)
        return None

    @property
    def value(self):
        """What's the current value of this configuration setting?

        If not present, the value may be inherited from some other
        ConfigurationSetting.
        """
        if self._value:
            # An explicitly set value always takes precedence.
            return self._value
        elif self.library_id and self.external_integration:
            # This is a library-specific specialization of an
            # ExternalIntegration. Treat the value set on the
            # ExternalIntegration as a default.
            return self.for_externalintegration(
                self.key, self.external_integration
            ).value
        elif self.library_id:
            # This is a library-specific setting. Treat the site-wide
            # value as a default.
            _db = Session.object_session(self)
            return self.sitewide(_db, self.key).value
        return self._value

    @value.setter
    def value(self, new_value):
        self._value = new_value

    def setdefault(self, default=None):
        """If no value is set, set it to `default`.
        Then return the current value.
        """
        if self.value is None:
            self.value = default
        return self.value

    @classmethod
    def _is_secret(self, key):
        """Should the value of the given key be treated as secret?

        This will have to do, in the absence of programmatic ways of
        saying that a specific setting should be treated as secret.
        """
        return any(
            key == x
            or key.startswith("%s_" % x)
            or key.endswith("_%s" % x)
            or ("_%s_" % x) in key
            for x in ("secret", "password")
        )

    @property
    def is_secret(self):
        """Should the value of this key be treated as secret?"""
        return self._is_secret(self.key)

    def value_or_default(self, default):
        """Return the value of this setting. If the value is None,
        set it to `default` and return that instead.
        """
        if self.value is None:
            self.value = default
        return self.value

    MEANS_YES = {"true", "t", "yes", "y"}

    @property
    def bool_value(self):
        """Turn the value into a boolean if possible.

        :return: A boolean, or None if there is no value.
        """
        if self.value:
            if self.value.lower() in self.MEANS_YES:
                return True
            return False
        return None

    @property
    def int_value(self):
        """Turn the value into an int if possible.

        :return: An integer, or None if there is no value.

        :raise ValueError: If the value cannot be converted to an int.
        """
        if self.value:
            return int(self.value)
        return None

    @property
    def float_value(self):
        """Turn the value into an float if possible.

        :return: A float, or None if there is no value.

        :raise ValueError: If the value cannot be converted to a float.
        """
        if self.value:
            return float(self.value)
        return None

    @property
    def json_value(self):
        """Interpret the value as JSON if possible.

        :return: An object, or None if there is no value.

        :raise ValueError: If the value cannot be parsed as JSON.
        """
        if self.value:
            return json.loads(self.value)
        return None
