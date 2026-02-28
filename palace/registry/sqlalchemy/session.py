from __future__ import annotations

import logging

from sqlalchemy import create_engine, exc as sa_exc
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from config import Configuration
from palace.registry.sqlalchemy.model import Base


def production_session():
    url = Configuration.database_url()
    logging.debug("Database url: %s", url)
    _db = SessionManager.session(url)

    # The first thing to do after getting a database connection is to
    # set up the logging configuration.
    #
    # If called during a unit test, this will configure logging
    # incorrectly, but 1) this method isn't normally called during
    # unit tests, and 2) package_setup() will call initialize() again
    # with the right arguments.
    from log import LogConfiguration

    LogConfiguration.initialize(_db)
    return _db


DEBUG = False


class SessionManager:

    engine_for_url = {}

    @classmethod
    def engine(cls, url=None):
        url = url or Configuration.database_url()
        return create_engine(url, echo=DEBUG)

    @classmethod
    def sessionmaker(cls, url=None):
        engine = cls.engine(url)
        return sessionmaker(bind=engine)

    @classmethod
    def initialize(cls, url: str) -> tuple[Engine, Connection]:
        """Initialize the database connection
        Create all the database tables from the models
        Optionally, run the alembic migration scripts
        :param db_url: The Database connection url"""
        if url in cls.engine_for_url:
            engine = cls.engine_for_url[url]
            return engine, engine.connect()

        engine = cls.engine(url)

        Base.metadata.create_all(engine)

        cls.engine_for_url[url] = engine
        return engine, engine.connect()

    @classmethod
    def session(cls, url):
        engine = connection = 0
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=sa_exc.SAWarning)
            engine, connection = cls.initialize(url)
        session = Session(connection)
        cls.initialize_data(session)
        session.commit()
        return session

    @classmethod
    def initialize_data(cls, session):
        pass
