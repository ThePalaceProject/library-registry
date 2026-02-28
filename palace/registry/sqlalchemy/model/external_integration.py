"""ExternalIntegration model for third-party API configuration."""

from __future__ import annotations

import logging

from sqlalchemy import Column, Integer, Unicode
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

from .base import Base


class ExternalIntegration(Base):
    """An external integration contains configuration for connecting
    to a third-party API.
    """

    # Possible goals of ExternalIntegrations.

    # These integrations are associated with external services such as
    # Adobe Vendor ID, which manage access to DRM-dependent content.
    DRM_GOAL = "drm"

    # Integrations with DRM_GOAL
    ADOBE_VENDOR_ID = "Adobe Vendor ID"

    # These integrations are associated with external services that
    # collect logs of server-side events.
    LOGGING_GOAL = "logging"

    # Integrations with LOGGING_GOAL
    INTERNAL_LOGGING = "Internal logging"
    LOGGLY = "Loggly"

    # These integrations are for sending email.
    EMAIL_GOAL = "email"

    # Integrations with EMAIL_GOAL
    SMTP = "SMTP"

    # If there is a special URL to use for access to this API,
    # put it here.
    URL = "url"

    # If access requires authentication, these settings represent the
    # username/password or key/secret combination necessary to
    # authenticate. If there's a secret but no key, it's stored in
    # 'password'.
    USERNAME = "username"
    PASSWORD = "password"

    __tablename__ = "externalintegrations"
    id = Column(Integer, primary_key=True)

    # Each integration should have a protocol (explaining what type of
    # code or network traffic we need to run to get things done) and a
    # goal (explaining the real-world goal of the integration).
    #
    # Basically, the protocol is the 'how' and the goal is the 'why'.
    protocol = Column(Unicode, nullable=False)
    goal = Column(Unicode, nullable=True)

    # A unique name for this ExternalIntegration. This is primarily
    # used to identify ExternalIntegrations from command-line scripts.
    name = Column(Unicode, nullable=True, unique=True)

    # Any additional configuration information goes into
    # ConfigurationSettings.
    settings = relationship(
        "ConfigurationSetting",
        backref="external_integration",
        lazy="joined",
        cascade="save-update, merge, delete, delete-orphan",
    )

    def __repr__(self):
        return "<ExternalIntegration: protocol=%s goal='%s' settings=%d ID=%d>" % (
            self.protocol,
            self.goal,
            len(self.settings),
            self.id,
        )

    @classmethod
    def lookup(cls, _db, protocol, goal):
        integrations = _db.query(cls).filter(cls.protocol == protocol, cls.goal == goal)

        integrations = integrations.all()
        if len(integrations) > 1:
            logging.warn(f"Multiple integrations found for '{protocol}'/'{goal}'")

        if not integrations:
            return None
        return integrations[0]

    @hybrid_property
    def url(self):
        return self.setting(self.URL).value

    @url.setter
    def url(self, new_url):
        self.set_setting(self.URL, new_url)

    @hybrid_property
    def username(self):
        return self.setting(self.USERNAME).value

    @username.setter
    def username(self, new_username):
        self.set_setting(self.USERNAME, new_username)

    @hybrid_property
    def password(self):
        return self.setting(self.PASSWORD).value

    @password.setter
    def password(self, new_password):
        return self.set_setting(self.PASSWORD, new_password)

    def set_setting(self, key, value):
        """Create or update a key-value setting for this ExternalIntegration."""
        setting = self.setting(key)
        setting.value = value
        return setting

    def setting(self, key):
        """Find or create a ConfigurationSetting on this ExternalIntegration.

        :param key: Name of the setting.
        :return: A ConfigurationSetting
        """
        from .configuration_setting import ConfigurationSetting

        return ConfigurationSetting.for_externalintegration(key, self)

    def explain(self, include_secrets=False):
        """Create a series of human-readable strings to explain an
        ExternalIntegration's settings.

        :param include_secrets: For security reasons,
           sensitive settings such as passwords are not displayed by default.

        :return: A list of explanatory strings.
        """
        lines = []
        lines.append("ID: %s" % self.id)
        if self.name:
            lines.append("Name: %s" % self.name)
        lines.append(f"Protocol/Goal: {self.protocol}/{self.goal}")

        def key(setting):
            if setting.library:
                return setting.key, setting.library.name
            return (setting.key, None)

        for setting in sorted(self.settings, key=key):
            explanation = f"{setting.key}='{setting.value}'"
            if setting.library:
                explanation = "{} (applies only to {})".format(
                    explanation,
                    setting.library.name,
                )
            if include_secrets or not setting.is_secret:
                lines.append(explanation)
        return lines
