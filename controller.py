import logging
from model import production_session
from config import (
    Configuration,
    CannotLoadConfiguration,
)
from util.app_server import HeartbeatController

class LibraryRegistry(object):

    def __init__(self, _db=None, testing=False):

        self.log = logging.getLogger("Content server web app")

        try:
            self.config = Configuration.load()
        except CannotLoadConfiguration, e:
            self.log.error("Could not load configuration file: %s" %e)
            sys.exit()

        if _db is None and not testing:
            _db = production_session()
        self._db = _db

        self.testing = testing

        self.setup_controllers()

    def setup_controllers(self):
        """Set up all the controllers that will be used by the web app."""
        self.library_registry = LibraryRegistryController(self)
        self.heartbeat = HeartbeatController()


class LibraryRegistryController(object):

    def __init__(self, app):
        self.app = app
        self._db = self.app._db

    def nearby(self):
        pass

    def search(self):
        pass
