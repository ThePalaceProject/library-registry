import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import MultipleResultsFound, NoResultFound

from library_registry.util.string_helpers import random_string


def generate_secret():
    """Generate a random secret."""
    return random_string(24)


def get_one(db, model, on_multiple='error', **kwargs):
    q = db.query(model).filter_by(**kwargs)
    try:
        return q.one()
    except MultipleResultsFound as e:
        if on_multiple == 'error':
            raise e
        elif on_multiple == 'interchangeable':
            # These records are interchangeable so we can use whichever one we want.
            #
            # This may be a sign of a problem somewhere else. A db-level constraint might be useful.
            q = q.limit(1)
            return q.one()
    except NoResultFound:
        return None


def get_one_or_create(db, model, create_method='', create_method_kwargs=None, **kwargs):
    one = get_one(db, model, **kwargs)
    if one:
        return (one, False)
    else:
        __transaction = db.begin_nested()
        try:
            if 'on_multiple' in kwargs:
                del kwargs['on_multiple']   # This kwarg is supported by get_one() but not by create().

            (obj, is_new) = create(db, model, create_method, create_method_kwargs, **kwargs)
            __transaction.commit()

            return (obj, is_new)

        except IntegrityError as e:
            logging.info("INTEGRITY ERROR on %r %r, %r: %r", model, create_method_kwargs, kwargs, e)
            __transaction.rollback()
            raise e


def create(db, model, create_method='', create_method_kwargs=None, **kwargs):
    kwargs.update(create_method_kwargs or {})
    created = getattr(model, create_method, model)(**kwargs)
    db.add(created)
    db.flush()
    return (created, True)
