from config import Configuration
import logging
import warnings
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    Unicode,
)
from sqlalchemy import (
    create_engine,
    exc as sa_exc,
    UniqueConstraint,
)
from sqlalchemy.exc import (
    IntegrityError
)
from sqlalchemy.ext.declarative import (
    declarative_base
)
from sqlalchemy.orm import (
    backref,
    relationship,
    sessionmaker,
)
from sqlalchemy.orm.exc import (
    NoResultFound,
    MultipleResultsFound,
)
from sqlalchemy.orm.session import Session
from sqlalchemy.sql.expression import cast

from geoalchemy2 import Geography

def production_session():
    url = Configuration.database_url()
    logging.debug("Database url: %s", url)
    return SessionManager.session(url)

DEBUG = False

class SessionManager(object):

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
    def initialize(cls, url):
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

def get_one(db, model, on_multiple='error', **kwargs):
    q = db.query(model).filter_by(**kwargs)
    try:
        return q.one()
    except MultipleResultsFound, e:
        if on_multiple == 'error':
            raise e
        elif on_multiple == 'interchangeable':
            # These records are interchangeable so we can use
            # whichever one we want.
            #
            # This may be a sign of a problem somewhere else. A
            # database-level constraint might be useful.
            q = q.limit(1)
            return q.one()
    except NoResultFound:
        return None

def get_one_or_create(db, model, create_method='',
                      create_method_kwargs=None,
                      **kwargs):
    one = get_one(db, model, **kwargs)
    if one:
        return one, False
    else:
        __transaction = db.begin_nested()
        try:
            if 'on_multiple' in kwargs:
                # This kwarg is supported by get_one() but not by create().
                del kwargs['on_multiple']
            obj = create(db, model, create_method, create_method_kwargs, **kwargs)
            __transaction.commit()
            return obj
        except IntegrityError, e:
            logging.info(
                "INTEGRITY ERROR on %r %r, %r: %r", model, create_method_kwargs, 
                kwargs, e)
            __transaction.rollback()
            return db.query(model).filter_by(**kwargs).one(), False

def create(db, model, create_method='',
           create_method_kwargs=None,
           **kwargs):
    kwargs.update(create_method_kwargs or {})
    created = getattr(model, create_method, model)(**kwargs)
    db.add(created)
    db.flush()
    return created, True

    
Base = declarative_base()

class Place(Base):
    __tablename__ = 'places'

    # These are the kinds of places we keep track of. These are not
    # supposed to be precise terms. Each census-designated place is
    # called a 'city', even if it's not a city in the legal sense.
    # Countries that call their top-level administrative divisions something
    # other than 'states' can still use 'state' as their type.
    NATION = 'nation'
    STATE = 'state'
    COUNTY = 'county'
    CITY = 'city'
    POSTAL_CODE = 'postal_code'
    LIBRARY_SERVICE_AREA = 'library_service_area'
    
    id = Column(Integer, primary_key=True)

    # The type of place.
    type = Column(Unicode(255), index=True, nullable=False)

    # The unique ID given to this place in the data source it was
    # derived from.
    external_id = Column(Unicode, index=True)

    # The name given to this place by the data source it was
    # derived from.
    external_name = Column(Unicode, index=True)

    # A canonical abbreviated name for this place. Generally used only
    # for nations and states.
    abbreviated_name = Column(Unicode, index=True)
    
    # The most convenient place that 'contains' this place. For most
    # places the most convenient parent will be a state. For states,
    # the best parent will be a nation. A nation has no parent.
    parent_id = Column(
        Integer, ForeignKey('places.id'), index=True
    )

    children = relationship(
        "Place",
        backref=backref("parent", remote_side = [id]),
        lazy="joined"
    )
    
    # The geography of the place itself. It is stored internally as a
    # geometry, which means we have to cast to Geography when doing
    # calculations.
    geography = Column(Geography(geometry_type='GEOMETRY'), nullable=False)

    aliases = relationship("PlaceAlias", backref='place')

    @property
    def geo(self):
        """Cast the .geography object to Geography for use in a database
        query. Otherwise it's sometimes treated as a Geometry object,
        which results in inaccurate measurements.

        TODO: I would prefer to do without this, but I don't
        understand enough about PostGIS/Geoalchemy to understand why
        Geography objects get treated as Geometry objects.
        """
        return cast(self.geography, Geography)
    
    def __repr__(self):
        if self.parent:
            parent = self.parent.external_name
        else:
            parent = None
        if self.abbreviated_name:
            abbr = "abbr=%s " % self.abbreviated_name
        else:
            abbr = ''
        output = u"<Place: %s type=%s %sexternal_id=%s parent=%s>" % (
            self.external_name, self.type, abbr, self.external_id, parent
        )
        return output.encode("utf8")


class PlaceAlias(Base):

    """An alternate name for a place."""
    __tablename__ = 'placealiases'

    id = Column(Integer, primary_key=True)
    place_id = Column(Integer, ForeignKey('places.id'), index=True)
    name = Column(Unicode, index=True)
    language = Column(Unicode(3), index=True)

    __table_args__ = (
        UniqueConstraint('place_id', 'name', 'language'),
    )
