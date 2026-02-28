"""Base SQLAlchemy setup and utility functions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, exc as sa_exc
from sqlalchemy.exc import IntegrityError, MultipleResultsFound, NoResultFound
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.orm.session import Session

from config import Configuration
from util.string_helpers import random_string

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine


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


def generate_secret():
    """Generate a random secret."""
    return random_string(24)


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


def get_one(db, model, on_multiple="error", **kwargs):
    q = db.query(model).filter_by(**kwargs)
    try:
        return q.one()
    except MultipleResultsFound as e:
        if on_multiple == "error":
            raise e
        elif on_multiple == "interchangeable":
            # These records are interchangeable so we can use
            # whichever one we want.
            #
            # This may be a sign of a problem somewhere else. A
            # database-level constraint might be useful.
            q = q.limit(1)
            return q.one()
    except NoResultFound:
        return None


def dump_query(query):
    from psycopg2.extensions import adapt as sqlescape
    from sqlalchemy.sql import compiler

    dialect = query.session.bind.dialect
    statement = query.statement
    comp = compiler.SQLCompiler(dialect, statement)
    comp.compile()
    enc = dialect.encoding
    params = {}
    for k, v in comp.params.items():
        if isinstance(v, str):
            v = v.encode(enc)
        params[k] = sqlescape(v)
    return (comp.string.encode(enc) % params).decode(enc)


def get_one_or_create(db, model, create_method="", create_method_kwargs=None, **kwargs):
    one = get_one(db, model, **kwargs)
    if one:
        return one, False
    else:
        __transaction = db.begin_nested()
        try:
            if "on_multiple" in kwargs:
                # This kwarg is supported by get_one() but not by create().
                del kwargs["on_multiple"]
            obj = create(db, model, create_method, create_method_kwargs, **kwargs)
            __transaction.commit()
            return obj
        except IntegrityError as e:
            logging.info(
                "INTEGRITY ERROR on %r %r, %r: %r",
                model,
                create_method_kwargs,
                kwargs,
                e,
            )
            __transaction.rollback()
            return db.query(model).filter_by(**kwargs).one(), False


def create(db, model, create_method="", create_method_kwargs=None, **kwargs):
    kwargs.update(create_method_kwargs or {})
    created = getattr(model, create_method, model)(**kwargs)
    db.add(created)
    db.flush()
    return created, True


Base = declarative_base()
