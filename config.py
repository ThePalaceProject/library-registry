import contextlib
import copy
import json
import os
import logging

@contextlib.contextmanager
def temp_config(new_config=None, replacement_classes=None):
    old_config = Configuration.instance
    replacement_classes = replacement_classes or [Configuration]
    if new_config is None:
        new_config = copy.deepcopy(old_config)
    try:
        for c in replacement_classes:
            c.instance = new_config
        yield new_config
    finally:
        for c in replacement_classes:
            c.instance = old_config

class CannotLoadConfiguration(Exception):
    pass

class Configuration(object):

    instance = None

    # Environment variables that contain URLs to the database
    DATABASE_TEST_ENVIRONMENT_VARIABLE = 'SIMPLIFIED_TEST_DATABASE'
    DATABASE_PRODUCTION_ENVIRONMENT_VARIABLE = 'SIMPLIFIED_PRODUCTION_DATABASE'
    
    log = logging.getLogger("Configuration file loader")
   
    INTEGRATIONS = 'integrations'

    BASE_URL = 'base_url'

    DATABASE_INTEGRATION = "Postgres"
    DATABASE_PRODUCTION_URL = "production_url"
    DATABASE_TEST_URL = "test_url"

    ADOBE_VENDOR_ID = "vendor_id"
    ADOBE_VENDOR_ID_NODE_VALUE = "node_value"
    ADOBE_VENDOR_ID_DELEGATE_URL = "delegate_url"

    REGISTRATION_TERMS_OF_SERVICE_TEXT = "registration_terms_of_service_text"
    
    @classmethod
    def load(cls):
        """Load additional site configuration from a config file.

        This is being phased out in favor of taking all configuration from a
        database.
        """
        cfv = 'SIMPLIFIED_CONFIGURATION_FILE'
        config_path = os.environ.get(cfv)
        if config_path:
            try:
                cls.log.info("Loading configuration from %s", config_path)
                configuration = cls._load(open(config_path).read())
            except Exception, e:
                raise CannotLoadConfiguration(
                    "Error loading configuration file %s: %s" % (
                        config_path, e)
                )
        else:
            configuration = cls._load('{}')
        cls.instance = configuration
        return configuration

    @classmethod
    def _load(cls, str):
        lines = [x for x in str.split("\n") if not x.strip().startswith("#")]
        return json.loads("\n".join(lines))
    
    # General getters
    @classmethod
    def get(cls, key, default=None):
        if not cls.instance:
            cls.load()
        return cls.instance.get(key, default)

    @classmethod
    def required(cls, key):
        if cls.instance:
            value = cls.get(key)
            if value is not None:
                return value
        raise ValueError(
            "Required configuration variable %s was not defined!" % key
        )

    @classmethod
    def integration(cls, name, required=False):
        """Find an integration configuration by name."""
        integrations = cls.get(cls.INTEGRATIONS, {})
        v = integrations.get(name, {})
        if not v and required:
            raise ValueError(
                "Required integration '%s' was not defined! I see: %r" % (
                    name, ", ".join(sorted(integrations.keys()))
                )
            )
        return v

    @classmethod
    def integration_url(cls, name, required=False):
        """Find the URL to an integration."""
        integration = cls.integration(name, required=required)
        v = integration.get(cls.URL, None)
        if not v and required:
            raise ValueError(
                "Integration '%s' did not define a required 'url'!" % name
            )
        return v

    # More specific getters.
    @classmethod
    def database_url(cls, test=False):
        if test:
            key = cls.DATABASE_TEST_URL
            environment_variable = cls.DATABASE_TEST_ENVIRONMENT_VARIABLE
        else:
            key = cls.DATABASE_PRODUCTION_URL
            environment_variable = cls.DATABASE_PRODUCTION_ENVIRONMENT_VARIABLE

        # Check the legacy location (the config file) first.
        url = None
        database_integration = cls.integration(cls.DATABASE_INTEGRATION)
        if database_integration:
            url = database_integration.get(config_key)

        # If that didn't work, check the new location (the environment
        # variable).
        if not url:
            url = os.environ.get(environment_variable)
        if not url:
            raise CannotLoadConfiguration(
                "Database URL was not defined in environment variable (%s) or configuration file." % environment_variable
            )
        return url

    @classmethod
    def vendor_id(cls, _db):
        """Look up the Adobe Vendor ID configuration for this registry.

        :return: a 3-tuple (vendor ID, node value, [delegates])
        """
        from model import ExternalIntegration

        integration = ExternalIntegration.lookup(
            _db, ExternalIntegration.ADOBE_VENDOR_ID,
            ExternalIntegration.DRM_GOAL)
        if not integration:
            return None, None, []
        setting = integration.setting(cls.ADOBE_VENDOR_ID_DELEGATE_URL)
        delegates = []
        try:
            delegates = setting.json_value or []
        except ValueError, e:
            cls.log.warn("Invalid Adobe Vendor ID delegates configured.")
        return (
            integration.setting(cls.ADOBE_VENDOR_ID).value,
            integration.setting(cls.ADOBE_VENDOR_ID_NODE_VALUE).int_value,
            delegates
        )
