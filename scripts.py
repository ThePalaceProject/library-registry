from nose.tools import set_trace
import argparse
import base64
import logging
import os
import re
import requests
import sys

from geometry_loader import GeometryLoader
from model import (
    get_one,
    get_one_or_create,
    production_session,
    Place,
    Library,
    LibraryAlias,
    ServiceArea,
)
from config import Configuration
from adobe_vendor_id import AdobeVendorIDClient

class Script(object):

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
        if stdin is not sys.stdin or not os.isatty(0):
            # A file has been redirected into standard input. Grab its
            # lines.
            lines = stdin
        else:
            lines = []
        return lines
    
    def __init__(self, _db=None):
        """Basic constructor.

        :_db: A database session to be used instead of
        creating a new one. Useful in tests.
        """
        if _db:
            self._session = _db

    def run(self):
        self.load_configuration()
        try:
            self.do_run()
        except Exception, e:
            logging.error(
                "Fatal exception while running script: %s", e,
                exc_info=e
            )
            raise e

    def load_configuration(self):
        if not Configuration.instance:
            Configuration.load()

            
class LoadPlacesScript(Script):
    
    @classmethod
    def parse_command_line(cls, _db=None, cmd_args=None, stdin=sys.stdin):
        parser = cls.arg_parser()
        parsed = parser.parse_args(cmd_args)
        stdin = cls.read_stdin_lines(stdin)
        return parsed, stdin
        
    def run(self, cmd_args=None, stdin=sys.stdin):
        parsed, stdin = self.parse_command_line(
            self._db, cmd_args, stdin
        )
        loader = GeometryLoader(self._db)
        a = 0
        for place, is_new in loader.load_ndjson(stdin):
            if is_new:
                what = 'NEW'
            else:
                what = 'UPD'
            print what, place
            a += 1
            if not a % 1000:
                self._db.commit()
        self._db.commit()


class SearchPlacesScript(Script):
    @classmethod
    def arg_parser(cls):
        parser = super(SearchPlacesScript, cls).arg_parser()
        parser.add_argument(
            'name', nargs='*', help='Place name to search for'
        )
        return parser

    def run(self, cmd_args=None, stdout=sys.stdout):
        parsed = self.parse_command_line(self._db, cmd_args)
        for place in self._db.query(Place).filter(
                Place.external_name.in_(parsed.name)
        ):
            stdout.write(repr(place))
            stdout.write("\n")


class SearchLibraryScript(Script):
    """Command-line interface to the library search."""
    @classmethod
    def arg_parser(cls):
        parser = super(SearchLibraryScript, cls).arg_parser()
        parser.add_argument(
            'query', nargs=1, help='Search query.'
        )
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
        parser.add_argument(
            '--name', help='Official name of the library', required=True
        )
        parser.add_argument(
            '--urn',
            help="URN used in the library's Authentication for OPDS document.",
            required=True
        )
        parser.add_argument(
            '--opds', help="URL of the library's OPDS server.",
            required=True
        )
        parser.add_argument('--alias', nargs='+', help='Alias for the library')
        parser.add_argument(
            '--description',
            help="Human-readable description of the library."
        )
        parser.add_argument(
            '--web', help="URL of the library's web server."
        )
        parser.add_argument(
            '--adobe-short-name', help="Short name of the library for Adobe Vendor ID purposes."
        )
        parser.add_argument(
            '--adobe-shared-secret', help="Shared secret between the library and the registry for Adobe Vendor ID purposes."
        )
        parser.add_argument('--place', nargs='+',
                            help="External ID of the library's service area.")
        return parser

    def run(self, cmd_args=None):
        parsed = self.parse_command_line(self._db, cmd_args)
        name = parsed.name
        urn = parsed.urn
        opds = parsed.opds
        web = parsed.web
        description = parsed.description
        aliases = parsed.alias
        places = parsed.place
        adobe_short_name = parsed.adobe_short_name
        adobe_shared_secret = parsed.adobe_shared_secret
        
        library, is_new = get_one_or_create(self._db, Library, urn=urn)
        if name:
            library.name = name
        if opds:
            library.opds_url = opds
        if web:
            library.web_url = web
        if description:
            library.description = description
        if adobe_short_name:
            library.adobe_short_name = adobe_short_name
        if adobe_shared_secret:
            library.adobe_shared_secret = adobe_shared_secret
        if aliases:
            for alias in aliases:
                get_one_or_create(self._db, LibraryAlias, library=library,
                                  name=alias, language='eng')
        if places:
            for place_external_id in places:
                place = get_one(self._db, Place, external_id=place_external_id)
                get_one_or_create(
                    self._db, ServiceArea, library=library, place=place
                )
        self._db.commit()


class AdobeVendorIDAcceptanceTestScript(Script):
    """Verify basic Adobe Vendor ID functionality, the way Adobe does
    when testing compliance.
    """
    
    @classmethod
    def arg_parser(cls):
        parser = super(AdobeVendorIDAcceptanceTestScript, cls).arg_parser()
        parser.add_argument(
            '--url', help='URL to the library registry', required=True
        )
        parser.add_argument(
            '--token', help='A short client token obtained from a library',
            required=True
        )
        return parser
        
    def run(self, cmd_args=None):
        parsed = self.parse_command_line(self._db, cmd_args)

        base_url = parsed.url
        if not base_url.endswith('/'):
            base_url += '/'
        base_url += 'AdobeAuth/'
        token = parsed.token

        client = AdobeVendorIDClient(base_url)
        
        print "1. Checking status: %s" % client.status_url
        response = client.status()
        # status() will raise an exception if anything is wrong.
        print 'OK Service is up and running.'
            
        print "2. Passing token into SignIn as authdata: %s" % client.signin_url
        identifier, label, content = client.sign_in_authdata(token)
        print "OK Found user identifier and label."
        print "   User identifier: %s" % identifier
        print "   Label: %s" % label
        print "   Full content: %s" % content

        print
        print "3. Passing token into SignIn as username/password."
        username, password = token.rsplit('|', 1)
        identifier, label, content = client.sign_in_standard(username, password)
        print "OK Found user identifier and label."
        print "   User identifier: %s" % identifier
        print "   Label: %s" % label
        print "   Full content: %s" % content
            
        print
        print "4. Passing identifier into UserInfo to get user info: %s" % client.accountinfo_url
        user_info, content = client.user_info(identifier)
        print "OK Found user info: %s" % user_info
        print "   Full content: %s" % content

