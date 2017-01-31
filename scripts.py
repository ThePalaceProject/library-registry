import argparse
import logging
import os
import sys

from geography_loader import GeographyLoader
from model import (
    production_session
)
from config import Configuration

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
        loader = GeographyLoader(self._db)
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
