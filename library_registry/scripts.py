import argparse
import json
import logging
import os
import sys

from library_registry.adobe_vendor_id import AdobeVendorIDClient
from library_registry.authentication_document import AuthenticationDocument
from library_registry.config import Configuration
from library_registry.emailer import Emailer, EmailTemplate
from library_registry.geometry_loader import GeometryLoader
from library_registry.model import (ConfigurationSetting, ExternalIntegration,
                                    Library, LibraryAlias, Place, ServiceArea,
                                    get_one, get_one_or_create,
                                    production_session)
from library_registry.registrar import LibraryRegistrar
from library_registry.util.problem_detail import ProblemDetail


class Script:
    @property
    def _db(self):
        if not hasattr(self, "_session"):
            self._session = production_session()
        return self._session

    @property
    def log(self):
        if not hasattr(self, '_log'):
            logger_name = getattr(self, 'name', None)
            self._log = logging.getLogger(logger_name)
        return self._log

    @classmethod
    def parse_command_line(cls, _db=None, cmd_args=None):
        parser = cls.arg_parser()
        return parser.parse_known_args(cmd_args)[0]

    @classmethod
    def arg_parser(cls):
        return argparse.ArgumentParser()

    @classmethod
    def read_stdin_lines(self, stdin):
        """Read lines from a (possibly mocked, possibly empty) standard input."""
        lines = []
        if stdin is not sys.stdin or not os.isatty(0):
            lines = stdin   # A file has been redirected into standard input. Grab its lines.

        return lines

    def __init__(self, _db=None):
        """
        Basic constructor.

        :_db: A database session to be used instead of creating a new one. Useful in tests.
        """
        if _db:
            self._session = _db

    def run(self):
        try:
            self.do_run()
        except Exception as e:
            logging.error("Fatal exception while running script: %s", e, exc_info=e)
            raise e


class LibraryScript(Script):
    """A script that operates on one library or all libraries."""

    # If this is True, the script will only ever operate on one library, and which library to use is a
    # required input. If this is False, the script can operate on a specific library, but if no library
    # is provided it will operate on all libraries.
    REQUIRES_SINGLE_LIBRARY = True

    @classmethod
    def arg_parser(cls):
        parser = super(LibraryScript, cls).arg_parser()
        parser.add_argument('--library', help='Official name of the library', required=cls.REQUIRES_SINGLE_LIBRARY)
        return parser

    def libraries(self, library_name=None):
        """
        Find all libraries on which this script should operate.
        :param library_name: The library name passed in on the command line, if any.
        """
        if library_name:
            library = get_one(self._db, Library, name=library_name)
            if not library:
                raise ValueError(f"No library with name '{library_name}'")
            return [library]
        return self.all_libraries

    @property
    def all_libraries(self):
        """
        Find an iterator over all libraries, whatever 'all libraries' means in the context of this script.

        By default, 'all libraries' means all libraries in the production or testing stages.
        """
        return self._db.query(Library).filter(Library._feed_restriction(production=False))


class LoadPlacesScript(Script):
    @classmethod
    def parse_command_line(cls, _db=None, cmd_args=None, stdin=sys.stdin):
        parser = cls.arg_parser()
        parsed = parser.parse_args(cmd_args)
        stdin = cls.read_stdin_lines(stdin)
        return parsed, stdin

    def run(self, cmd_args=None, stdin=sys.stdin):
        parsed, stdin = self.parse_command_line(self._db, cmd_args, stdin)
        loader = GeometryLoader(self._db)
        a = 0

        for place, is_new in loader.load_ndjson(stdin):
            if is_new:
                what = 'NEW'
            else:
                what = 'UPD'

            print(what, place)
            a += 1

            if not a % 1000:
                self._db.commit()

        self._db.commit()


class SearchPlacesScript(Script):
    @classmethod
    def arg_parser(cls):
        parser = super(SearchPlacesScript, cls).arg_parser()
        parser.add_argument('name', nargs='*', help='Place name to search for')
        return parser

    def run(self, cmd_args=None, stdout=sys.stdout):
        parsed = self.parse_command_line(self._db, cmd_args)
        for place in self._db.query(Place).filter(Place.external_name.in_(parsed.name)):
            stdout.write(repr(place))
            stdout.write("\n")


class SearchLibraryScript(Script):
    """Command-line interface to the library search."""
    @classmethod
    def arg_parser(cls):
        parser = super(SearchLibraryScript, cls).arg_parser()
        parser.add_argument('query', nargs=1, help='Search query.')
        return parser

    def run(self, cmd_args=None, stdout=sys.stdout):
        parsed = self.parse_command_line(self._db, cmd_args)
        for library in Library.search(self._db, None, parsed.query[0]):
            stdout.write("%s: %s" % (library.name, library.opds_url))
            stdout.write("\n")


class AddLibraryScript(Script):
    @classmethod
    def arg_parser(cls):
        parser = super(AddLibraryScript, cls).arg_parser()
        parser.add_argument('--name', help='Official name of the library', required=True)
        parser.add_argument('--authentication-url', help="URL to the library's Authentication for OPDS document.",
                            required=True)
        parser.add_argument('--opds', help="URL of the library's OPDS server.", required=True)
        parser.add_argument('--alias', nargs='+', help='Alias for the library')
        parser.add_argument('--description', help="Human-readable description of the library.")
        parser.add_argument('--web', help="URL of the library's web server.")
        parser.add_argument('--short-name', help="Short name of the library for Adobe Vendor ID purposes.")
        parser.add_argument('--shared-secret',
                            help="Shared secret between the library and the registry for Adobe Vendor ID purposes.")
        parser.add_argument('--place', nargs='+', help="External ID of the library's service area.")

        return parser

    def run(self, cmd_args=None):
        parsed = self.parse_command_line(self._db, cmd_args)
        name = parsed.name
        authentication_url = parsed.authentication_url
        opds = parsed.opds
        web = parsed.web
        description = parsed.description
        aliases = parsed.alias
        places = parsed.place
        short_name = parsed.short_name
        shared_secret = parsed.shared_secret
        library, is_new = get_one_or_create(self._db, Library, authentication_url=authentication_url)

        if name:
            library.name = name
        if opds:
            library.opds_url = opds
        if web:
            library.web_url = web
        if description:
            library.description = description
        if short_name:
            library.short_name = short_name
        if shared_secret:
            library.shared_secret = shared_secret
        if aliases:
            for alias in aliases:
                get_one_or_create(self._db, LibraryAlias, library=library,
                                  name=alias, language='eng')
        if places:
            for place_external_id in places:
                place = get_one(self._db, Place, external_id=place_external_id)
                get_one_or_create(self._db, ServiceArea, library=library, place=place)

        self._db.commit()


class SetCoverageAreaScript(LibraryScript):

    @classmethod
    def arg_parser(cls):
        parser = super(SetCoverageAreaScript, cls).arg_parser()
        parser.add_argument(
            '--service-area',
            help=(
                "JSON document or string describing the library's service area. If no value is specified, "
                "it is assumed to be the same as --focus-area."
            )
        )
        parser.add_argument(
            '--focus-area',
            help=(
                "JSON document or string describing the library's focus area. If no value is specified, "
                "it is assumed to be the same as --service-area."
            )
        )
        return parser

    def run(self, cmd_args=None, place_class=Place):
        parsed = self.parse_command_line(self._db, cmd_args)

        [library] = self.libraries(parsed.library)

        if not parsed.service_area and not parsed.focus_area:
            logging.info("No new coverage areas specified, doing nothing.")
            self.report(library)
            return

        service_area = parsed.service_area
        focus_area = parsed.focus_area
        # If the areas make sense as JSON, parse them. Otherwise a string will be interpreted as a single place name.
        try:
            service_area = json.loads(service_area)
        except (ValueError, TypeError):
            ...

        try:
            focus_area = json.loads(focus_area)
        except (ValueError, TypeError):
            ...

        service_area, focus_area = AuthenticationDocument.parse_service_and_focus_area(
            self._db, service_area, focus_area, place_class
        )
        for (valid, unknown, ambiguous) in [service_area, focus_area]:
            if unknown:
                raise ValueError("Unknown places: %r" % list(unknown.items()))
            if ambiguous:
                raise ValueError("Ambiguous places: %r" % list(unknown.items()))

        AuthenticationDocument.set_service_areas(library, service_area, focus_area)
        self._db.commit()
        self.report(library)

    def report(self, library):
        logging.info("Service areas for %s:", library.name)
        for area in library.service_areas:
            logging.info("%s: %r", area.type, area.place)


class RegistrationRefreshScript(LibraryScript):
    """Refresh our view of every library in the system based on their current authentication document"""
    REQUIRES_SINGLE_LIBRARY = False

    def run(self, cmd_args=None):
        parsed = self.parse_command_line(self._db, cmd_args)
        registrar = self.registrar
        for library in self.libraries(parsed.library):
            result = registrar.reregister(library)
            if isinstance(result, ProblemDetail):
                error_msg = (
                    f"FAILURE {library.name} ({library.authentication_url}) "
                    f"uri={result.uri}, title={result.title}, detail={result.detail}, debug={result.debug_message}"
                )
                self.log.error(error_msg)
            else:
                self.log.info(f"SUCCESS {library.name} ({library.authentication_url})")
                self._db.commit()

    @property
    def registrar(self):
        """Overridable method to create a LibraryRegistrar."""
        return LibraryRegistrar(self._db)


class AdobeVendorIDAcceptanceTestScript(Script):
    """Verify basic Adobe Vendor ID functionality, the way Adobe does when testing compliance"""

    @classmethod
    def arg_parser(cls):
        parser = super(AdobeVendorIDAcceptanceTestScript, cls).arg_parser()
        parser.add_argument('--url', help='URL to the library registry', required=True)
        parser.add_argument('--token', help='A short client token obtained from a library', required=True)

        return parser

    def run(self, cmd_args=None):
        parsed = self.parse_command_line(self._db, cmd_args)

        base_url = parsed.url
        if not base_url.endswith('/'):
            base_url += '/'

        base_url += 'AdobeAuth/'
        token = parsed.token

        client = AdobeVendorIDClient(base_url)

        print(f"1. Checking status: {client.status_url}")
        client.status()     # status() will raise an exception if anything is wrong.
        print("OK Service is up and running.")

        print(f"2. Passing token into SignIn as authdata: {client.signin_url}")
        identifier, label, content = client.sign_in_authdata(token)
        print("OK Found user identifier and label.")
        print(f"   User identifier: {identifier}")
        print(f"   Label: {label}")
        print(f"   Full content: {content}")

        print()
        print("3. Passing token into SignIn as username/password.")
        username, password = token.rsplit('|', 1)
        identifier, label, content = client.sign_in_standard(username, password)
        print("OK Found user identifier and label.")
        print(f"   User identifier: {identifier}")
        print(f"   Label: {label}")
        print(f"   Full content: {content}")

        print()
        print(f"4. Passing identifier into UserInfo to get user info: {client.accountinfo_url}")
        user_info, content = client.user_info(identifier)
        print(f"OK Found user info: {user_info}")
        print(f"   Full content: {content}")


class ConfigurationSettingScript(Script):

    @classmethod
    def _parse_setting(self, setting):
        """Parse a command-line setting option into a key-value pair."""
        if '=' not in setting:
            raise ValueError(f'Incorrect format for setting: "{setting}". Should be "key=value"')

        return setting.split('=', 1)

    @classmethod
    def add_setting_argument(self, parser, help):
        """Modify an ArgumentParser to indicate that the script takes command-line settings"""
        parser.add_argument('--setting', help=help, action="append")

    def apply_settings(self, settings, obj):
        """Treat `settings` as a list of command-line argument settings, and apply each one to `obj`"""
        if not settings:
            return None

        for setting in settings:
            key, value = self._parse_setting(setting)
            obj.setting(key).value = value


class ConfigureSiteScript(ConfigurationSettingScript):
    """View or update site-wide configuration."""

    @classmethod
    def arg_parser(cls):
        parser = argparse.ArgumentParser()
        parser.add_argument('--show-secrets', help="Include secrets when displaying site settings.",
                            action="store_true", default=False)
        cls.add_setting_argument(
            parser, 'Set a site-wide setting, such as base_url. Format: --setting="base_url=http://localhost:7000"'
        )
        return parser

    def do_run(self, _db=None, cmd_args=None, output=sys.stdout):
        _db = _db or self._db
        args = self.parse_command_line(_db, cmd_args=cmd_args)

        if args.setting:
            for setting in args.setting:
                key, value = self._parse_setting(setting)
                ConfigurationSetting.sitewide(_db, key).value = value

        settings = _db.query(ConfigurationSetting).filter(
            ConfigurationSetting.library_id == None).filter(
                ConfigurationSetting.external_integration == None
            ).order_by(ConfigurationSetting.key)

        output.write("Current site-wide settings:\n")

        for setting in settings:
            if args.show_secrets or not setting.is_secret:
                output.write(f"{setting.key}='{setting.value}'\n")

        _db.commit()


class ShowIntegrationsScript(Script):
    """Show information about the external integrations on a server."""

    name = "List the external integrations on this server."

    @classmethod
    def arg_parser(cls):
        parser = argparse.ArgumentParser()
        parser.add_argument('--name', help='Only display information for the integration with the given name or ID')
        parser.add_argument('--show-secrets', help='Display secret values such as passwords.', action='store_true')
        return parser

    def do_run(self, _db=None, cmd_args=None, output=sys.stdout):
        _db = _db or self._db
        args = self.parse_command_line(_db, cmd_args=cmd_args)

        if args.name:
            name = args.name
            integration = get_one(_db, ExternalIntegration, name=name)
            if not integration:
                integration = get_one(_db, ExternalIntegration, id=name)
            if integration:
                integrations = [integration]
            else:
                output.write(f"Could not locate integration by name or ID: {args}\n")
                integrations = []
        else:
            integrations = _db.query(ExternalIntegration).order_by(
                    ExternalIntegration.name, ExternalIntegration.id).all()

        if not integrations:
            output.write("No integrations found.\n")
        for integration in integrations:
            output.write("\n".join(integration.explain(include_secrets=args.show_secrets)))
            output.write("\n")


class ConfigureIntegrationScript(ConfigurationSettingScript):
    """Create a integration or change its settings."""
    name = "Create a site-wide integration or change an integration's settings"

    @classmethod
    def parse_command_line(cls, _db=None, cmd_args=None):
        parser = cls.arg_parser(_db)
        return parser.parse_known_args(cmd_args)[0]

    @classmethod
    def arg_parser(cls, _db):
        parser = argparse.ArgumentParser()
        parser.add_argument('--name', help='Name of the integration')
        parser.add_argument('--id', help='ID of the integration, if it has no name')
        parser.add_argument('--protocol', help='Protocol used by the integration.')
        parser.add_argument('--goal', help='Goal of the integration')
        cls.add_setting_argument(parser, 'Set a configuration value on the integration. Format: --setting="key=value"')
        return parser

    @classmethod
    def _integration(self, _db, id, name, protocol, goal):
        """Find or create the ExternalIntegration referred to."""
        if not id and not name and not (protocol and goal):
            err_msg = "An integration must by identified by either ID, name, or the combination of protocol and goal."
            raise ValueError(err_msg)

        integration = None

        if id:
            integration = get_one(_db, ExternalIntegration, ExternalIntegration.id == id)
            if not integration:
                raise ValueError("No integration with ID %s." % id)

        if name:
            integration = get_one(_db, ExternalIntegration, name=name)
            if not integration and not (protocol and goal):
                err_msg = f'No integration with name "{name}". To create it, you must also provide protocol and goal.'
                raise ValueError(err_msg)

        if not integration and (protocol and goal):
            integration, is_new = get_one_or_create(_db, ExternalIntegration, protocol=protocol, goal=goal)

        if name:
            integration.name = name

        return integration

    def do_run(self, _db=None, cmd_args=None, output=sys.stdout):
        _db = _db or self._db
        args = self.parse_command_line(_db, cmd_args=cmd_args)

        # Find or create the integration
        protocol = None
        id = args.id
        name = args.name
        protocol = args.protocol
        goal = args.goal
        integration = self._integration(_db, id, name, protocol, goal)
        self.apply_settings(args.setting, integration)
        _db.commit()
        output.write("Configuration settings stored.\n")
        output.write("\n".join(integration.explain()))
        output.write("\n")


class ConfigureVendorIDScript(Script):
    """Configure the site-wide Adobe Vendor ID configuration."""
    @classmethod
    def arg_parser(cls):
        parser = argparse.ArgumentParser()
        parser.add_argument("--vendor-id", help="Vendor ID issued by Adobe", required=True)
        parser.add_argument("--node-value", help="Node value issued by Adobe", required=True)
        parser.add_argument("--delegate", help="Delegate Adobe IDs to this URL if no local answer found",
                            action="append")
        return parser

    def do_run(self, _db=None, cmd_args=None, output=sys.stdout):
        _db = _db or self._db
        parsed = self.parse_command_line(_db, cmd_args=cmd_args)

        integration, is_new = get_one_or_create(
            _db, ExternalIntegration, goal=ExternalIntegration.DRM_GOAL,
            protocol=ExternalIntegration.ADOBE_VENDOR_ID
        )
        c = Configuration

        # All node values are string representations of hexidecimal numbers.
        int(parsed.node_value, 16)   # This line MUST be present or the following bits fail. TODO: find out why

        integration.setting(c.ADOBE_VENDOR_ID).value = parsed.vendor_id
        integration.setting(c.ADOBE_VENDOR_ID_NODE_VALUE).value = parsed.node_value
        delegates = parsed.delegate
        for delegate in delegates:
            if not delegate.endswith("/AdobeAuth/"):
                raise ValueError(f'Invalid delegate: {delegate}. Expected something ending with "/AdobeAuth/"')
        integration.setting(Configuration.ADOBE_VENDOR_ID_DELEGATE_URL).value = json.dumps(delegates)

        _db.commit()


class ConfigureEmailerScript(Script):
    """Configure the site-wide email configuration and send a test email to verify it"""

    @classmethod
    def arg_parser(cls):
        parser = argparse.ArgumentParser()
        parser.add_argument("--host", help="SMTP host", required=True)
        parser.add_argument("--port", help="SMTP port", default=587, type=int)
        parser.add_argument("--username", help="SMTP username", required=True)
        parser.add_argument("--password", help="SMTP password", required=True)
        parser.add_argument("--from-address", help="Email sent will come from this address", required=True)
        parser.add_argument("--from-name", help="Name associated with the from-address", required=True)
        parser.add_argument("--test-address", help="Send a test email to this address", required=True)
        return parser

    def do_run(self, _db=None, cmd_args=None, output=sys.stdout, emailer_class=Emailer):
        _db = _db or self._db
        parsed = self.parse_command_line(_db, cmd_args=cmd_args)

        integration, is_new = get_one_or_create(
            _db, ExternalIntegration, goal=ExternalIntegration.EMAIL_GOAL,
            protocol=ExternalIntegration.SMTP
        )
        integration.setting(Emailer.PORT).value = parsed.port
        integration.username = parsed.username
        integration.password = parsed.password
        integration.url = parsed.host
        integration.setting(Emailer.FROM_ADDRESS).value = parsed.from_address
        integration.setting(Emailer.FROM_NAME).value = parsed.from_name

        emailer = emailer_class.from_sitewide_integration(_db)
        template = EmailTemplate("Test email", "This is a test email.")
        emailer.templates["test"] = template
        emailer.send("test", parsed.test_address)

        # Since the emailer didn't raise an exception we can assume we sent
        # the email successfully.
        _db.commit()
