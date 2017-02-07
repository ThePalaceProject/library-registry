import json
import os
import logging

class CannotLoadConfiguration(Exception):
    pass

class Configuration(object):

    instance = None
    
    log = logging.getLogger("Configuration file loader")

    # Logging stuff
    LOGGING = "logging"
    LOGGING_LEVEL = "level"
    LOGGING_FORMAT = "format"
    LOG_FORMAT_TEXT = "text"
    LOG_FORMAT_JSON = "json"

   
    INTEGRATIONS = 'integrations'
    LIBRARY_REGISTRY_INTEGRATION = 'Library Registry'
    URL = 'url'
    DATABASE_INTEGRATION = "Postgres"
    DATABASE_PRODUCTION_URL = "production_url"
    DATABASE_TEST_URL = "test_url"

    @classmethod
    def load(cls):
        cfv = 'SIMPLIFIED_CONFIGURATION_FILE'
        if not cfv in os.environ:
            raise CannotLoadConfiguration(
                "No configuration file defined in %s." % cfv)

        config_path = os.environ[cfv]
        try:
            cls.log.info("Loading configuration from %s", config_path)
            configuration = cls._load(open(config_path).read())
        except Exception, e:
            raise CannotLoadConfiguration(
                "Error loading configuration file %s: %s" % (
                    config_path, e)
            )
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
        else:
            key = cls.DATABASE_PRODUCTION_URL
        return cls.integration(cls.DATABASE_INTEGRATION)[key]

    @classmethod
    def logging_policy(cls):
        default_logging = {}
        return cls.get(cls.LOGGING, default_logging)
