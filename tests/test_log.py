import logging

import pytest

from log import JSONFormatter, LogConfiguration, LogglyHandler, StringFormatter
from model import ExternalIntegration

from . import DatabaseTest


class TestLogConfiguration(DatabaseTest):

    def loggly_integration(self):
        """Create an ExternalIntegration for a Loggly account."""
        integration = self._external_integration(
            protocol=ExternalIntegration.LOGGLY,
            goal=ExternalIntegration.LOGGING_GOAL
        )
        integration.url = "http://example.com/%s/"
        integration.password = "a_token"
        return integration

    def test_from_configuration(self):
        cls = LogConfiguration
        m = cls.from_configuration

        # When logging is configured on initial startup, with no
        # database connection, these are the defaults.
        internal_log_level, database_log_level, [handler] = m(
            None, testing=False
        )
        assert internal_log_level == cls.INFO
        assert database_log_level == cls.WARN
        assert isinstance(handler.formatter, JSONFormatter)

        # The same defaults hold when there is a database connection
        # but nothing is actually configured.
        internal_log_level, database_log_level, [handler] = m(
            self._db, testing=False
        )
        assert internal_log_level == cls.INFO
        assert database_log_level == cls.WARN
        assert isinstance(handler.formatter, JSONFormatter)

        # Let's set up a Loggly integration and change the defaults.
        loggly = self.loggly_integration()
        internal = self._external_integration(
            protocol=ExternalIntegration.INTERNAL_LOGGING,
            goal=ExternalIntegration.LOGGING_GOAL
        )
        internal.setting(cls.LOG_LEVEL).value = cls.ERROR
        internal.setting(cls.LOG_FORMAT).value = cls.TEXT_LOG_FORMAT
        internal.setting(cls.DATABASE_LOG_LEVEL).value = cls.DEBUG
        template = "%(filename)s:%(message)s"
        internal.setting(cls.LOG_MESSAGE_TEMPLATE).value = template
        internal_log_level, database_log_level, handlers = m(
            self._db, testing=False
        )
        assert internal_log_level == cls.ERROR
        assert database_log_level == cls.DEBUG
        [loggly_handler] = [x for x in handlers if isinstance(x, LogglyHandler)]
        assert loggly_handler.url == "http://example.com/a_token/"

        [stream_handler] = [x for x in handlers
                            if isinstance(x, logging.StreamHandler)]
        assert isinstance(stream_handler.formatter, StringFormatter)
        assert stream_handler.formatter._fmt == template

        # If testing=True, then the database configuration is ignored,
        # and the log setup is one that's appropriate for display
        # alongside unit test output.
        internal_log_level, database_log_level, [handler] = m(
            self._db, testing=True
        )
        assert internal_log_level == cls.DEBUG
        assert database_log_level == cls.WARN
        assert handler.formatter._fmt == cls.DEFAULT_MESSAGE_TEMPLATE

    def test_defaults(self):
        cls = LogConfiguration
        template = cls.DEFAULT_MESSAGE_TEMPLATE

        # Normally the default log level is INFO and log messages are
        # emitted in JSON format.
        assert cls._defaults(testing=False) == (cls.INFO, cls.JSON_LOG_FORMAT, cls.WARN, cls.DEFAULT_MESSAGE_TEMPLATE)

        # When we're running unit tests, the default log level is DEBUG
        # and log messages are emitted in text format.
        assert cls._defaults(testing=True) == (cls.DEBUG, cls.TEXT_LOG_FORMAT, cls.WARN, cls.DEFAULT_MESSAGE_TEMPLATE)

    def test_set_formatter(self):
        # Create a generic handler.
        handler = logging.StreamHandler()

        # Configure it for text output.
        template = '%(filename)s:%(message)s'
        LogConfiguration.set_formatter(
            handler, LogConfiguration.TEXT_LOG_FORMAT, template
        )
        formatter = handler.formatter
        assert isinstance(formatter, StringFormatter)
        assert formatter._fmt == template

        # Configure a similar handler for JSON output.
        handler = logging.StreamHandler()
        LogConfiguration.set_formatter(
            handler, LogConfiguration.JSON_LOG_FORMAT, template
        )
        formatter = handler.formatter
        assert isinstance(formatter, JSONFormatter)

        # In this case the template is irrelevant. The JSONFormatter
        # uses the default format template, but it doesn't matter,
        # because JSONFormatter overrides the format() method.
        assert formatter._fmt == '%(message)s'

        # Configure a handler for output to Loggly. In this case
        # the format and template are irrelevant.
        handler = LogglyHandler("no-such-url")
        LogConfiguration.set_formatter(handler, None, None)
        assert isinstance(formatter, JSONFormatter)

    def test_loggly_handler(self):
        """Turn an appropriate ExternalIntegration into a LogglyHandler."""

        integration = self.loggly_integration()
        handler = LogConfiguration.loggly_handler(integration)
        assert isinstance(handler, LogglyHandler)
        assert handler.url == "http://example.com/a_token/"

        # Remove the loggly handler's .url, and the default URL will
        # be used.
        integration.url = None
        handler = LogConfiguration.loggly_handler(integration)
        assert handler.url == LogConfiguration.DEFAULT_LOGGLY_URL % dict(token="a_token")

    def test_interpolate_loggly_url(self):
        m = LogConfiguration._interpolate_loggly_url

        # We support two string interpolation techniques for combining
        # a token with a URL.
        assert m("http://foo/%s/bar/", "token") == "http://foo/token/bar/"
        assert m("http://foo/%(token)s/bar/", "token") == "http://foo/token/bar/"

        # If the URL contains no string interpolation, we assume the token's
        # already in there.
        assert m("http://foo/othertoken/bar/", "token") == "http://foo/othertoken/bar/"

        # Anything that doesn't fall under one of these cases will raise an
        # exception.
        with pytest.raises(TypeError):
            m("http://%s/%s", "token")

        with pytest.raises(KeyError):
            m("http://%(atoken)s/", "token")

