from datetime import datetime
import logging
import os
from nose.tools import (
    set_trace
)
from sqlalchemy.orm.session import Session

from config import Configuration
from model import (
    Base,
    Place,
    SessionManager,
)

def package_setup():
    """Make sure the database schema is initialized and initial
    data is in place.
    """
    engine, connection = DatabaseTest.get_database_connection()

    # First, recreate the schema.
    for table in reversed(Base.metadata.sorted_tables):
        engine.execute(table.delete())

    Base.metadata.create_all(connection)

    # Initialize basic database data needed by the application.
    _db = Session(connection)
    SessionManager.initialize_data(_db)
    _db.commit()
    connection.close()
    engine.dispose()

    if not Configuration.instance:
        Configuration.load()

class DatabaseTest(object):

    engine = None
    connection = None

    @classmethod
    def get_database_connection(cls):
        url = Configuration.database_url(test=True)
        engine, connection = SessionManager.initialize(url)

        return engine, connection

    @classmethod
    def setup_class(cls):
        cls.engine, cls.connection = cls.get_database_connection()
        os.environ['TESTING'] = 'true'

    @classmethod
    def teardown_class(cls):
        # Destroy the database connection and engine.
        cls.connection.close()
        cls.engine.dispose()
        if 'TESTING' in os.environ:
            del os.environ['TESTING']

    def setup(self):
        # Create a new connection to the database.
        self._db = Session(self.connection)
        self.transaction = self.connection.begin_nested()

        # Start with a high number so it won't interfere with tests that
        # search for a small number.
        self.counter = 2000

        self.time_counter = datetime(2014, 1, 1)

    def teardown(self):
        # Close the session.
        self._db.close()

        # Roll back all database changes that happened during this
        # test, whether in the session that was just closed or some
        # other session.
        self.transaction.rollback()

    @property
    def _id(self):
        self.counter += 1
        return self.counter

    @property
    def _str(self):
        return unicode(self._id)

    @property
    def _time(self):
        v = self.time_counter 
        self.time_counter = self.time_counter + timedelta(days=1)
        return v
