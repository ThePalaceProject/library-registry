from config import Configuration
import logging
from nose.tools import set_trace
import re
import warnings
from psycopg2.extensions import adapt as sqlescape
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    Unicode,
)
from sqlalchemy import (
    create_engine,
    exc as sa_exc,
    func,
    or_,
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
from sqlalchemy.sql import compiler
from sqlalchemy.sql.expression import cast

from geoalchemy2 import Geography, Geometry

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

def dump_query(query):
    dialect = query.session.bind.dialect
    statement = query.statement
    comp = compiler.SQLCompiler(dialect, statement)
    comp.compile()
    enc = dialect.encoding
    params = {}
    for k,v in comp.params.iteritems():
        if isinstance(v, unicode):
            v = v.encode(enc)
        params[k] = sqlescape(v)
    return (comp.string.encode(enc) % params).decode(enc)
    
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

class Library(Base):
    """An entry in this table corresponds more or less to an OPDS server.

    Most libraries are designed to serve everyone in a specific list
    of Places. (These are the ones we support now).

    TODO: Eventually a Library will be able to specify a list of
    Audiences as well. This will allow us to search for or filter
    libraries that don't serve absolutely everyone in their service
    area.
    """
    __tablename__ = 'libraries'

    id = Column(Integer, primary_key=True)
    
    # The official name of the library.
    name = Column(Unicode, index=True)

    aliases = relationship("LibraryAlias", backref='library')
    service_areas = relationship('ServiceArea', backref='library')

    @classmethod
    def point(cls, latitude, longitude):
        """Convert latitude/longitude to a string that can be
        used as a Geometry.
        """
        return 'SRID=4326;POINT (%s %s)' % (longitude, latitude)
    
    @classmethod
    def nearby(cls, _db, latitude, longitude, max_radius=150):
        """Find libraries whose service areas include or are close to the
        given point.

        :param latitude: The latitude component of the starting point.
        :param longitude: The longitude component of the starting point.
        :param max_radius: How far out from the starting point to search
            for a library's service area, in kilometers.

        :return: A database query that returns lists of 2-tuples
        (library, distance from starting point). Distances are
        measured in meters.
        """

        # We start with a single point on the globe. Call this Point
        # A.
        target = cls.point(latitude, longitude)
        target_geography = cast(target, Geography)

        # Find another point on the globe that's 150 kilometers
        # northeast of Point A. Call this Point B.
        other_point = func.ST_Project(
            target_geography, max_radius*1000, func.radians(90.0)
        )
        other_point = cast(other_point, Geometry)

        # Determine the distance between Point A and Point B, in
        # radians. (150 kilometers is a different number of radians in
        # different parts of the world.)
        distance_to_other_point = func.ST_Distance(target, other_point)

        # Find all Places that are no further away from A than that
        # number of radians.
        nearby = func.ST_DWithin(target,
                                 Place.geometry,
                                 distance_to_other_point)

        # For each such place, calculate the distance to Point A in
        # meters.
        distance = func.ST_Distance_Sphere(target, Place.geometry)
        
        qu = _db.query(Library).join(Library.service_areas).join(
            ServiceArea.place).filter(nearby).add_column(distance).order_by(
                distance.asc())
        return qu

    @classmethod
    def search(cls, _db, latitude, longitude, query):
        """Try as hard as possible to find a small number of libraries
        that match the given query.

        Preference will be given to libraries closer to the current
        latitude/longitude.
        """
        if not query:
            # No query, no results.
            return []
        here = cls.point(latitude, longitude)

        library_query, place_query, place_type = cls.query_parts(query)

        # We start with libraries that match the name query.
        if library_query:
            libraries_for_name = cls.search_by_name(_db, library_query, here)
        else:
            libraries_for_name = []
            
        # We tack on any additional libraries that match a place query.
        if place_query:
            libraries_by_location = cls.search_by_location(
                _db, place_name, place_type, here,
                libraries_by_name
            )
        else:
            libraries_for_location = []
            
        return libraries_for_name + libraries_for_location

    @classmethod
    def search_by_name(cls, _db, name, here=None):
        """Find libraries whose name or alias matches the given name.

        :param name: Name of the library to search for.
        :param here: Order results by proximity to this location.
        """
       
        qu = _db.query(Library).outerjoin(Library.aliases)
        if here:
            qu = qu.join(Library.service_areas).outerjoin(ServiceArea.place)

        name_matches = cls.fuzzy_match(Library.name, name)
        alias_matches = cls.fuzzy_match(LibraryAlias.name, name)
        qu = qu.filter(or_(name_matches, alias_matches))

        if here:
            qu = qu.order_by(func.ST_Distance_Sphere(here, Place.geometry))
        return qu

    @classmethod
    def search_by_location(cls, _db, query, type, here=None,
                           exclude_libraries=[]):
        """Find libraries whose service area includes a place with
        the given name.

        :param query: Name of the place to search for.
        :param type: Restrict results to places of this type.
        :param here: Order results by proximity to this location.
        :param exclude_libraries: A list of Libraries to exclude from
         results (because they were picked up earlier by a
         higher-priority query).
        """
        qu = _db.query(Library).join(Library.service_areas).join(
            ServiceArea.place
        )
        qu = qu.filter(cls.fuzzy_match(Place.name, query))
        if type:
            qu = qu.filter(Place.type==type)
        if exclude_libraries:
            exclude_ids = [x.id for x in exclude_libraries]
            qu = qu.filter(~Library.id.in_(exclude_ids))
        if here:
            qu = qu.order_by(func.ST_Distance_Sphere(here, Place.geometry))
        return qu
    
    us_zip = re.compile("^[0-9]{5}$")
    us_zip_plus_4 = re.compile("^[0-9]{5}-[0-9]{4}$")
    running_whitespace = re.compile("\s+")

    @classmethod
    def query_cleanup(cls, query):
        """Clean up a query."""
        query = query.lower()
        query = cls.running_whitespace.sub(" ", query).strip()

        # Correct the most common misspelling of 'library'.
        query = query.replace("libary", "library")
        return query

    @classmethod
    def as_postal_code(cls, query):
        """Try to interpret a query as a postal code."""
        if cls.us_zip.match(query):
            return query
        match = cls.us_zip_plus_4.match(query)
        if match:
            return query[:5]
    
    @classmethod
    def query_parts(cls, query):
        """Turn a query received by a user into a set of things to
        check against different bits of the database.
        """
        query = cls.query_cleanup(query)

        postal_code = cls.as_postal_code(query)
        if postal_code:
            # The query is a postal code. Don't even bother searching
            # for a library name -- just find that code.
            return None, postal_code, Place.POSTAL_CODE

        # In theory, absolutely anything could be a library name or
        # alias. We'll let Levenshtein distance take care of minor
        # typos, but we don't process the query very much before
        # seeing if it matches a library name.
        library_query = query

        # If the query looks like a library name, extract a location
        # from it. This will find the public library in Irvine from
        # "irvine public library", even though there is no library
        # called the "Irvine Public Library".
        #
        # NOTE: This will fall down if there is a place with "Library"
        # in the name, but there are no such places in the US.
        place_query = query
        place_type = None
        for indicator in 'public library', 'library':
            if indicator in place_query:
                place_query = place_query.replace(indicator, '').strip()

        if place_query.endswith(' county'):
            # It's common for someone to search for e.g. 'kern county
            # library'. If we have a library system named after the
            # county, it will show up in the library name search. But
            # we should also look up counties with that name and find
            # all the libraries that cover some part of one of those
            # counties.
            place_query = place_query[:-7]
            place_type = Place.COUNTY
        
        return library_query, place_query, place_type
    
    @classmethod
    def fuzzy_match(cls, field, value):
        """Create a SQL clause that attempts a fuzzy match of the given
        field against the given value.

        If the field's value is less than six characters, we require
        an exact (case-insensitive) match. Otherwise, we require a
        Levenshtein distance of less than two between the field value and
        the provided value.
        """
        is_long = func.length(field) >= 6
        close_enough = func.levenshtein(func.lower(field), value) < 2
        long_value_is_approximate_match = (is_long & close_enough)
        exact_match = field.ilike(value)
        return or_(long_value_is_approximate_match, exact_match)


class LibraryAlias(Base):

    """An alternate name for a library."""
    __tablename__ = 'libraryalias'

    id = Column(Integer, primary_key=True)
    library_id = Column(Integer, ForeignKey('libraries.id'), index=True)
    name = Column(Unicode, index=True)
    language = Column(Unicode(3), index=True)

    __table_args__ = (
        UniqueConstraint('library_id', 'name', 'language'),
    )

    
class ServiceArea(Base):
    """Designates a geographic area served by a Library.

    A ServiceArea maps a Library to a Place. People living in this
    Place have service from the Library.
    """
    __tablename__ = 'serviceareas'
   
    id = Column(Integer, primary_key=True)
    library_id = Column(
        Integer, ForeignKey('libraries.id'), index=True
    )

    place_id = Column(
        Integer, ForeignKey('places.id'), index=True
    )

    __table_args__ = (
        UniqueConstraint('library_id', 'place_id'),
    )
    

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
    geometry = Column(Geometry(srid=4326), nullable=False)

    aliases = relationship("PlaceAlias", backref='place')

    service_areas = relationship("ServiceArea", backref="place")
    
    def served_by(self):
        """Find all Libraries with a ServiceArea whose Place intersects
        this Place.

        A Library whose ServiceArea borders this place, but does not
        intersect this place, is not counted. This way, the state
        library from the next state over doesn't count as serving your
        state.
        """
        _db = Session.object_session(self)
        intersects = Place.geometry.intersects(self.geometry)
        does_not_touch = func.ST_Touches(Place.geometry, self.geometry) == False
        qu = _db.query(Library).join(Library.service_areas).join(
            ServiceArea.place).filter(intersects).filter(does_not_touch)
        return qu
    
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
