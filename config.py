import contextlib
import copy
import logging
import os
from pathlib import Path

from attr import dataclass


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


class CannotSendEmail(Exception):
    pass


def _url_from_environment(environment_variable: str) -> str | None:
    url = os.environ.get(environment_variable)
    if url is not None:
        return url

    # Try loading the URL in pieces. This is necessary in CI.
    scheme = os.environ.get(environment_variable + "_SCHEME")
    host = os.environ.get(environment_variable + "_HOST")
    port = os.environ.get(environment_variable + "_PORT")
    user = os.environ.get(environment_variable + "_USER")
    password = os.environ.get(environment_variable + "_PASSWORD")

    if scheme and host and port:
        if user and password:
            host = f"{user}:{password}@" + host
        return f"{scheme}://{host}:{port}"

    return None


class Configuration:
    DATADIR = Path(os.path.dirname(__file__)) / "data"

    instance = None

    # Environment variables that contain URLs to the database
    DATABASE_TEST_ENVIRONMENT_VARIABLE = "SIMPLIFIED_TEST_DATABASE"
    DATABASE_PRODUCTION_ENVIRONMENT_VARIABLE = "SIMPLIFIED_PRODUCTION_DATABASE"

    log = logging.getLogger("Configuration file loader")

    INTEGRATIONS = "integrations"

    BASE_URL = "base_url"

    ADOBE_VENDOR_ID = "vendor_id"
    ADOBE_VENDOR_ID_NODE_VALUE = "node_value"
    ADOBE_VENDOR_ID_DELEGATE_URL = "delegate_url"

    # The URL to the document containing the terms of service for
    # library registration
    REGISTRATION_TERMS_OF_SERVICE_URL = "registration_terms_of_service_url"

    # An HTML snippet describing the terms of service for library
    # registration. It's better if this is a short snippet of text
    # with a link, rather than the actual text of the terms of
    # service.
    REGISTRATION_TERMS_OF_SERVICE_HTML = "registration_terms_of_service_html"

    # Email sent by the library registry will be 'from' this address,
    # and receipients will be invited to contact this address if they
    # have problems.
    REGISTRY_CONTACT_EMAIL = "registry_contact_email"

    # If the registry provides access to a web-based client, it can
    # specify the URL with this setting. The URL must be templated and contain
    # a `{uuid}` expression, to provide the web URL for a specific library.
    WEB_CLIENT_URL = "web_client_url"

    # If a library references a place that's not explicitly in any particular
    # nation, we assume that they're talking about this nation.
    DEFAULT_NATION_ABBREVIATION = "default_nation_abbreviation"

    # For performance reasons, a registry may want to omit certain
    # pieces of information from large feeds. This sitewide setting
    # controls how big a feed must be to be considered 'large'.
    LARGE_FEED_SIZE = "large_feed_size"

    # The name of the sitewide secret used for admin login.
    SECRET_KEY = "secret_key"

    # AWS credentials
    AWS_S3_BUCKET_NAME = "SIMPLIFIED_AWS_S3_BUCKET_NAME"
    AWS_S3_ENDPOINT_URL = "SIMPLIFIED_AWS_S3_ENDPOINT_URL"

    @classmethod
    def database_url(cls, test=False):
        """Find the URL to the database so that other configuration
        settings can be looked up.
        """
        if test:
            environment_variable = cls.DATABASE_TEST_ENVIRONMENT_VARIABLE
        else:
            environment_variable = cls.DATABASE_PRODUCTION_ENVIRONMENT_VARIABLE

        url = _url_from_environment(environment_variable)
        if not url:
            raise CannotLoadConfiguration(
                "Database URL was not defined in environment variable (%s) or configuration file."
                % environment_variable
            )
        return url

    @classmethod
    def vendor_id(cls, _db):
        """Look up the Adobe Vendor ID configuration for this registry.

        :return: a 3-tuple (vendor ID, node value, [delegates])
        """
        from model import ExternalIntegration

        integration = ExternalIntegration.lookup(
            _db, ExternalIntegration.ADOBE_VENDOR_ID, ExternalIntegration.DRM_GOAL
        )
        if not integration:
            return None, None, []
        setting = integration.setting(cls.ADOBE_VENDOR_ID_DELEGATE_URL)
        delegates = []
        try:
            delegates = setting.json_value or []
        except ValueError:
            cls.log.warn("Invalid Adobe Vendor ID delegates configured.")

        node = integration.setting(cls.ADOBE_VENDOR_ID_NODE_VALUE).value
        if node:
            node = int(node, 16)
        return (
            integration.setting(cls.ADOBE_VENDOR_ID).value,
            node,
            delegates,
        )

    @classmethod
    def aws_config(cls) -> "AWSConfig":
        """Return the AWS configurations setup in the environment"""
        return AWSConfig(
            bucket_name=os.environ.get(cls.AWS_S3_BUCKET_NAME),
            endpoint_url=_url_from_environment(cls.AWS_S3_ENDPOINT_URL),
        )


@dataclass
class AWSConfig:
    bucket_name: str = None
    endpoint_url: str = None
