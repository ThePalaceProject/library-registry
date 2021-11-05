import logging

import pytest

from library_registry.log import (
    StringFormatter,
    JSONFormatter,
    LogglyHandler,
    LogConfiguration,
)
from library_registry.model import ExternalIntegration


@pytest.fixture
def loggly_integration(db_session, create_test_external_integration):
    """Create an ExternalIntegration for a Loggly account."""
    integration = create_test_external_integration(
        db_session,
        protocol=ExternalIntegration.LOGGLY,
        goal=ExternalIntegration.LOGGING_GOAL
    )
    integration.url = "http://example.com/%s/"
    integration.password = "a_token"
    yield integration
    db_session.delete(integration)
    db_session.commit()


class TestLogConfiguration:
    def test_from_configuration_no_db_connection(self):
        """
        GIVEN: Nothing
        WHEN:  LogConfiguration.from_configuration() is called
        THEN:  A log configuration with these default settings should return:
                 - internal log level of INFO
                 - database log level of WARN
                 - a single handler using the JSONFormatter
        """
        (
            internal_log_level,
            database_log_level,
            handlers
        ) = LogConfiguration.from_configuration(None, testing=False)
        assert internal_log_level == LogConfiguration.INFO
        assert database_log_level == LogConfiguration.WARN
        assert len(handlers) == 1
        assert isinstance(handlers[0].formatter, JSONFormatter)

    def test_from_configuration_db_conn_no_config(self, db_session):
        """
        GIVEN: A database connection but no set configuration
        WHEN:  LogConfiguration.from_configuration is called
        THEN:  A log configuration with these settings should return:
                 - internal log level of INFO
                 - database log level of WARN
                 - a single handler using the JSONFormatter
        """
        # The same defaults hold when there is a database connection but nothing is actually configured.
        (
            internal_log_level,
            database_log_level,
            handlers
        ) = LogConfiguration.from_configuration(db_session, testing=False)
        assert internal_log_level == LogConfiguration.INFO
        assert database_log_level == LogConfiguration.WARN
        assert isinstance(handlers[0].formatter, JSONFormatter)

    def test_from_configuration_with_loggly_integration(
        self, db_session, create_test_external_integration, loggly_integration
    ):
        # Let's set up a Loggly integration and change the defaults.
        internal = create_test_external_integration(
            db_session,
            protocol=ExternalIntegration.INTERNAL_LOGGING,
            goal=ExternalIntegration.LOGGING_GOAL
        )
        internal.setting(LogConfiguration.LOG_LEVEL).value = LogConfiguration.ERROR
        internal.setting(LogConfiguration.LOG_FORMAT).value = LogConfiguration.TEXT_LOG_FORMAT
        internal.setting(LogConfiguration.DATABASE_LOG_LEVEL).value = LogConfiguration.DEBUG
        template = "%(filename)s:%(message)s"
        internal.setting(LogConfiguration.LOG_MESSAGE_TEMPLATE).value = template

        (
            internal_log_level,
            database_log_level,
            handlers
        ) = LogConfiguration.from_configuration(db_session, testing=False)

        assert internal_log_level == LogConfiguration.ERROR
        assert database_log_level == LogConfiguration.DEBUG
        [loggly_handler] = [x for x in handlers if isinstance(x, LogglyHandler)]
        assert loggly_handler.url == "http://example.com/a_token/"

        [stream_handler] = [x for x in handlers if isinstance(x, logging.StreamHandler)]
        assert isinstance(stream_handler.formatter, StringFormatter)
        assert stream_handler.formatter._fmt == template

        # If testing=True, then the database configuration is ignored, and the log setup is one
        # that's appropriate for display alongside unit test output.
        internal_log_level, database_log_level, [handler] = LogConfiguration.from_configuration(
            db_session, testing=True
        )
        assert internal_log_level == LogConfiguration.DEBUG
        assert database_log_level == LogConfiguration.WARN
        assert handler.formatter._fmt == LogConfiguration.DEFAULT_MESSAGE_TEMPLATE

        db_session.delete(internal)
        db_session.commit()

    def test__defaults(self):
        # Normally the default log level is INFO and log messages are emitted in JSON format.
        assert LogConfiguration._defaults(testing=False) == (
            LogConfiguration.INFO,
            LogConfiguration.JSON_LOG_FORMAT,
            LogConfiguration.WARN,
            LogConfiguration.DEFAULT_MESSAGE_TEMPLATE
        )

        # When we're running unit tests, the default log level is DEBUG and log messages are emitted in text format.
        assert LogConfiguration._defaults(testing=True) == (
            LogConfiguration.DEBUG,
            LogConfiguration.TEXT_LOG_FORMAT,
            LogConfiguration.WARN,
            LogConfiguration.DEFAULT_MESSAGE_TEMPLATE
        )

    def test_set_formatter(self):
        handler = logging.StreamHandler()   # Create a generic handler.

        # Configure it for text output.
        template = '%(filename)s:%(message)s'
        LogConfiguration.set_formatter(handler, LogConfiguration.TEXT_LOG_FORMAT, template)
        formatter = handler.formatter
        assert isinstance(formatter, StringFormatter)
        assert formatter._fmt == template

        # Configure a similar handler for JSON output.
        handler = logging.StreamHandler()
        LogConfiguration.set_formatter(handler, LogConfiguration.JSON_LOG_FORMAT, template)
        formatter = handler.formatter
        assert isinstance(formatter, JSONFormatter)

        # In this case the template is irrelevant. The JSONFormatter uses the default format template,
        # but it doesn't matter, because JSONFormatter overrides the format() method.
        assert formatter._fmt == '%(message)s'

        # Configure a handler for output to Loggly. In this case the format and template are irrelevant.
        handler = LogglyHandler("no-such-url")
        LogConfiguration.set_formatter(handler, None, None)
        assert isinstance(formatter, JSONFormatter)

    def test_loggly_handler(self, loggly_integration):
        """Turn an appropriate ExternalIntegration into a LogglyHandler."""
        integration = loggly_integration
        handler = LogConfiguration.loggly_handler(integration)
        assert isinstance(handler, LogglyHandler)
        assert handler.url == "http://example.com/a_token/"

        # Remove the loggly handler's .url, and the default URL will be used.
        integration.url = None
        handler = LogConfiguration.loggly_handler(integration)
        assert handler.url == LogConfiguration.DEFAULT_LOGGLY_URL % dict(token="a_token")

    def test__interpolate_loggly_url(self):
        m = LogConfiguration._interpolate_loggly_url

        # We support two string interpolation techniques for combining a token with a URL.
        assert m("http://foo/%s/bar/", "token") == "http://foo/token/bar/"
        assert m("http://foo/%(token)s/bar/", "token") == "http://foo/token/bar/"

        # If the URL contains no string interpolation, we assume the token's already in there.
        assert m("http://foo/othertoken/bar/", "token") == "http://foo/othertoken/bar/"

        # Anything that doesn't fall under one of these cases will raise an exception.
        with pytest.raises(TypeError):
            m("http://%s/%s", "token")

        with pytest.raises(KeyError):
            m("http://%(atoken)s/", "token")
