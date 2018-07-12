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

    ADOBE_VENDOR_ID = "vendor_id"
    ADOBE_VENDOR_ID_NODE_VALUE = "node_value"
    ADOBE_VENDOR_ID_DELEGATE_URL = "delegate_url"

    # The text to which users must agree to register a library.
    REGISTRATION_TERMS_OF_SERVICE_TEXT = "registration_terms_of_service_text"

    # Email sent by the library registry will be 'from' this address,
    # and receipients will be invited to contact this address if they
    # have problems.
    REGISTRY_CONTACT_EMAIL = "registry_contact_email"
    
    @classmethod
    def database_url(cls, test=False):
        """Find the URL to the database so that other configuration
        settings can be looked up.
        """
        if test:
            environment_variable = cls.DATABASE_TEST_ENVIRONMENT_VARIABLE
        else:
            environment_variable = cls.DATABASE_PRODUCTION_ENVIRONMENT_VARIABLE

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
            integration.setting(cls.ADOBE_VENDOR_ID_NODE_VALUE).value,
            delegates,
        )
