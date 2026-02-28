from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError, MultipleResultsFound, NoResultFound

from util.string_helpers import random_string


def generate_secret():
    """Generate a random secret."""
    return random_string(24)


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
