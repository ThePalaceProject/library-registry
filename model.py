import base64
from config import Configuration
import datetime
import logging
from nose.tools import set_trace
import re
import json
import uuid
import warnings
from psycopg2.extensions import adapt as sqlescape
from sqlalchemy import (
    Binary,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
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
    aliased,
    backref,
    relationship,
    sessionmaker,
    validates,
)
from sqlalchemy.orm.exc import (
    NoResultFound,
    MultipleResultsFound,
)
from sqlalchemy.orm.session import Session
from sqlalchemy.sql import compiler
from sqlalchemy.sql.expression import cast
from sqlalchemy.ext.hybrid import hybrid_property

from geoalchemy2 import Geography, Geometry

from util import (
    GeometryUtility,
)
from util.short_client_token import ShortClientTokenTool

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

    # A URN that uniquely identifies the library. This is the URN
    # served by the library's Authentication for OPDS document.
    urn = Column(Unicode, index=True)
    
    # Human-readable explanation of who the library serves.
    description = Column(Unicode)

    # The URL to the library's OPDS server.
    opds_url = Column(Unicode)

    # The URL to the library's web page.
    web_url = Column(Unicode)
    
    # When the library's record was last updated.
    timestamp = Column(DateTime, index=True,
                       default=lambda: datetime.datetime.utcnow(),
                       onupdate=lambda: datetime.datetime.utcnow())

    # The library's logo.
    logo = Column(Unicode)

    # To issue Adobe IDs for this library, the registry must share a
    # short name and a secret with them.

    adobe_short_name = Column(Unicode, index=True)
    adobe_shared_secret = Column(Unicode)

    aliases = relationship("LibraryAlias", backref='library')
    delegated_patron_identifiers = relationship(
        "DelegatedPatronIdentifier", backref='library'
    )
    service_areas = relationship('ServiceArea', backref='library')

    __table_args__ = (
        UniqueConstraint('urn'),
        UniqueConstraint('adobe_short_name'),
    )

    @validates('adobe_short_name')
    def validate_adobe_short_name(self, key, value):
        if not value:
            return value
        if '|' in value:
            raise ValueError(
                'Adobe short name cannot contain the pipe character.'
            )
        return value.upper()
    
    @classmethod
    def nearby(cls, _db, target, max_radius=150):
        """Find libraries whose service areas include or are close to the
        given point.

        :param target: The starting point. May be a Geometry object or
         a 2-tuple (latitude, longitude).
        :param max_radius: How far out from the starting point to search
            for a library's service area, in kilometers.

        :return: A database query that returns lists of 2-tuples
        (library, distance from starting point). Distances are
        measured in meters.
        """

        # We start with a single point on the globe. Call this Point
        # A.
        if isinstance(target, tuple):
            target = GeometryUtility.point(*target)
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

        # For each library served by such a place, calculate the
        # minimum distance between the library's service area and
        # Point A in meters.
        min_distance = func.min(func.ST_Distance_Sphere(target, Place.geometry))
        
        qu = _db.query(Library).join(Library.service_areas).join(
            ServiceArea.place).filter(nearby).add_column(
                min_distance).group_by(Library.id).order_by(
                min_distance.asc())
        return qu

    @classmethod
    def search(cls, _db, target, query):
        """Try as hard as possible to find a small number of libraries
        that match the given query.

        :param target: Order libraries by their distance from this
         point. May be a Geometry object or a 2-tuple (latitude,
         longitude).
        """
        # We don't anticipate a lot of libraries or a lot of
        # localities with the same name, but we need to have _some_
        # kind of limit just to place an upper bound on how bad things
        # can get. This will guarantee we never return more than 20
        # results.
        max_libraries = 10
        
        if not query:
            # No query, no results.
            return []
        if target:
            if isinstance(target, tuple):
                here = GeometryUtility.point(*target)
            else:
                here = target
        else:
            here = None
            
        library_query, place_query, place_type = cls.query_parts(query)
        # We start with libraries that match the name query.
        if library_query:
            libraries_for_name = cls.search_by_library_name(
                _db, library_query, here).limit(max_libraries).all()
        else:
            libraries_for_name = []
            
        # We tack on any additional libraries that match a place query.
        if place_query:
            libraries_for_location = cls.search_by_location_name(
                _db, place_query, place_type, here,
            ).limit(max_libraries).all()
        else:
            libraries_for_location = []

        if libraries_for_name and libraries_for_location:
            # Filter out any libraries that show up in both lists.
            for_name = set(libraries_for_name)
            libraries_for_location = [
                x for x in libraries_for_location if not x in for_name
            ]
        return libraries_for_name + libraries_for_location

    @classmethod
    def search_by_library_name(cls, _db, name, here=None):
        """Find libraries whose name or alias matches the given name.

        :param name: Name of the library to search for.
        :param here: Order results by proximity to this location.
        """
       
        qu = _db.query(Library).outerjoin(Library.aliases)
        if here:
            qu = qu.outerjoin(Library.service_areas).outerjoin(ServiceArea.place)

        name_matches = cls.fuzzy_match(Library.name, name)
        alias_matches = cls.fuzzy_match(LibraryAlias.name, name)
        qu = qu.filter(or_(name_matches, alias_matches))

        if here:
            # Order by the minimum distance between one of the
            # library's service areas and the current location.
            min_distance = func.min(
                func.ST_Distance_Sphere(here, Place.geometry)
            )
            qu = qu.add_column(min_distance)
            qu = qu.group_by(Library.id)
            qu = qu.order_by(min_distance.asc())
        return qu

    @classmethod
    def search_by_location_name(cls, _db, query, type=None, here=None):
        """Find libraries whose service area overlaps a place with
        the given name.

        :param query: Name of the place to search for.
        :param type: Restrict results to places of this type.
        :param here: Order results by proximity to this location.
        :param exclude_libraries: A list of Libraries to exclude from
         results (because they were picked up earlier by a
         higher-priority query).
        """
        # For a library to match, the Place named by the query must
        # intersect a Place served by that library.
        named_place = aliased(Place)
        qu = _db.query(Library).join(
            Library.service_areas).join(
                ServiceArea.place).join(
                    named_place,
                    func.ST_Intersects(Place.geometry, named_place.geometry)
                ).outerjoin(named_place.aliases)

        name_match = cls.fuzzy_match(named_place.external_name, query)
        alias_match = cls.fuzzy_match(PlaceAlias.name, query)
        qu = qu.filter(or_(name_match, alias_match))
        if type:
            qu = qu.filter(named_place.type==type)
        if here:
            min_distance = func.min(func.ST_Distance_Sphere(here, named_place.geometry))
            qu = qu.add_column(min_distance)
            qu = qu.group_by(Library.id)
            qu = qu.order_by(min_distance.asc())
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

        place_query, place_type = Place.parse_place_name(place_query)

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
        close_enough = func.levenshtein(func.lower(field), value) <= 2
        long_value_is_approximate_match = (is_long & close_enough)
        exact_match = field.ilike(value)
        return or_(long_value_is_approximate_match, exact_match)

    @property
    def urn_uri(self):
        "Return the URN as a urn: URI."
        if self.urn.startswith('urn:'):
            return self.urn
        else:
            return 'urn:' + self.urn
    
    @property
    def logo_data_uri(self):
        """Return the logo as a data: URI."""
        if not self.logo:
            return None
        return "data:image/png;base64,%s" % base64.b64encode(self.logo)


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
    EVERYWHERE = 'everywhere'
    
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
    # the best parent will be a nation. A nation has no parent; neither
    # does 'everywhere'.
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

    @classmethod
    def everywhere(self, _db):
        """Return a special Place that represents everywhere."""
        place = get_one_or_create(
            _db, Place, type=self.EVERYWHERE,
            create_method_kwargs=dict(external_id="Everywhere",
                                      external_name="Everywhere")
        )
        # TODO: We need to allow this Place to exist without a
        # geometry.

    @classmethod
    def parse_place_name(cls, place_name):
        """Try to extract a place type from a name.

        :return: A 2-tule (place_name, place_type)

        e.g. "Kern County" becomes ("Kern", Place.COUNTY)
        "Arizona State" becomes ("Arizona", Place.STATE)
        "Chicago" becaomes ("Chicago", None)
        """
        check = place_name.lower()
        if check.endswith(' county'):
            place_name = place_query[:-7]
            place_type = Place.COUNTY

        if check.endswith(' state'):
            place_name = place_query[:-6]
            place_type = Place.STATE
        return place_name, place_type

    @classmethod
    def lookup_by_name(cls, _db, name, place_type=None):
        """Look up one or more Places by name.

        TODO: We need to support "Chicago, IL"
        """
        if not place_type:
            name, place_type = cls.parse_place_name(name)
        qu = _db.query(Place).outerjoin(PlaceAlias).filter(
            or_(Place.external_name==name, Place.abbreviated_name==name,
                PlaceAlias.alias==name)
        )
        if place_type:
            qu = qu.filter(Place.type==place_type)
        return qu

    @classmethod
    def name_parts(cls, name):
        """Split a nested geographic name into parts.

        "Boston, MA" is split into ["MA", "Boston"]
        "Lake County, Ohio, USA" is split into
        ["USA", "Ohio", "Lake County"]

        There is no guarantee that these place names correspond to
        Places in the database.

        :param name: The name to split into parts.
        :return: A list of place names, with the largest place at the front
           of the list.
        """
        return [x.strip() for x in reverse(name.split(","))]
    
    @classmethod
    def strictly_inside(cls, qu, place):
        """Modifies a filter to find places inside the given Place but not
        bordering it.

        Connecticut does not overlap New York, but they intersect
        because the two states share a border. This method creates a
        more real-world notion of 'inside' that does not count a
        shared border.
        """
        intersects = Place.geometry.intersects(inside.geometry)
        touches = func.ST_Touches(Place.geometry, inside.geometry)
        return qu.filter(intersects).filter(touches==False)
    
    @classmethod
    def lookup_inside(cls, _db, name, must_be_inside):
        """Look up a named Place geographically within another Place.

        :param must_be_inside: A Place object representing the place
        to look inside.

        :return: A Place object, or None if no match could be found.
        :raise MultipleResultsFound: If more than one Place with the
        given name is inside `place`.
        """
        parts = cls.name_parts(name)
        if len(parts) > 1:
            # We're in a situation where we're trying to look up a
            # scoped name inside a Place object, e.g. looking for
            # "Boston, MA" inside the US. `name_parts` has turned "Boston,
            # MA" into ["MA", "Boston"].
            #
            # Now we need to look for "MA" inside the US, and then
            # look for "Boston" inside the object representing
            # Massachussets.
            for part in parts:
                must_be_inside = cls.lookup_inside(_db, part, must_be_inside)
                if not must_be_inside:
                    # A link in the chain has failed. Return None
                    # immediately.
                    return None
            # Every link in the chain has succeeded, and `must_be_inside`
            # now contains the Place we were looking for.
            return must_be_inside

        # If we get here, it means we're looking up "Boston" within
        # Massachussets, or "Kern County" within the United States.
        # In other words, we expect to find at most one place with
        # this name inside the `must_be_inside` object.
        #
        # If we find more than one, it's an error. The name should
        # have been scoped better. This will happen if you search for
        # "Springfield" or "Lake County" within the United States,
        # instead of specifying which state you're talking about.
        qu = cls.lookup_by_name(_db, name)
        if must_be_inside.type != cls.EVERYWHERE:
            qu = cls.strictly_inside(qu, place)
        places = qu.all()
        if len(places) == 0:
            return None
        if len(places) > 1:
            raise MultipleResultsFound(
                "More than one place called %s inside %s." % (
                    name, must_be_inside.external_name
                )
            )
        
    def served_by(self):
        """Find all Libraries with a ServiceArea whose Place intersects
        this Place.

        A Library whose ServiceArea borders this place, but does not
        intersect this place, is not counted. This way, the state
        library from the next state over doesn't count as serving your
        state.
        """
        _db = Session.object_session(self)
        qu = _db.query(Library).join(Library.service_areas).join(
            ServiceArea.place)
        qu = self.strictly_inside(qu, self.geometry)
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


class DelegatedPatronIdentifier(Base):
    """An identifier generated by the library registry which identifies a
    patron of one of the libraries.

    This is probably an Adobe Account ID.
    """
    ADOBE_ACCOUNT_ID = u'Adobe Account ID'
    
    __tablename__ = 'delegatedpatronidentifiers'
    id = Column(Integer, primary_key=True)
    type = Column(String(255), index=True)
    library_id = Column(Integer, ForeignKey('libraries.id'), index=True)

    # This is the ID the foreign library gives us when referring to
    # this patron.
    patron_identifier = Column(String(255), index=True)

    # This is the identifier we made up for the patron. This is what the
    # foreign library is trying to look up.
    delegated_identifier = Column(String)
    
    __table_args__ = (
        UniqueConstraint('type', 'library_id', 'patron_identifier'),
    )

    @classmethod
    def get_one_or_create(
            cls, _db, library, patron_identifier, identifier_type,
            create_function
    ):
        """Look up the delegated identifier for the given patron. If there is
        none, create one.

        :param library: The Library in charge of the patron's record.

        :param patron_identifier: An identifier used by that library
         to distinguish between this patron and others. This should be
         an identifier created solely for the purpose of identifying
         the patron with the library registry, and not (e.g.) the
         patron's barcode.

        :param identifier_type: The type of the delegated identifier
         to look up. (probably ADOBE_ACCOUNT_ID)

        :param create_function: If this patron does not have a
         DelegatedPatronIdentifier, one will be created, and the given
         function will be called to determine the value of
         DelegatedPatronIdentifier.delegated_identifier.

        :return: A 2-tuple (DelegatedPatronIdentifier, is_new)
        """
        identifier, is_new = get_one_or_create(
            _db, DelegatedPatronIdentifier, library=library,
            patron_identifier=patron_identifier, type=identifier_type
        )
        if is_new:
            identifier.delegated_identifier = create_function()
        return identifier, is_new


class ShortClientTokenDecoder(ShortClientTokenTool):
    """Turn a short client token into a DelegatedPatronIdentifier.

    Used by the library registry. Not used by the circulation manager.
    
    See util/short_client_token.py for the corresponding encoder.
    """

    def uuid(self):
        """Create a new UUID URN compatible with the Vendor ID system."""
        u = str(uuid.uuid1(self.node_value))
        # This chop is required by the spec. I have no idea why, but
        # since the first part of the UUID is the least significant,
        # it doesn't do much damage.
        value = "urn:uuid:0" + u[1:]
        return value
    
    def __init__(self, node_value):
        super(ShortClientTokenDecoder, self).__init__()
        self.node_value = node_value
    
    def decode(self, _db, token):
        """Decode a short client token.

        :return: a DelegatedPatronIdentifier

        :raise ValueError: When the token is not valid for any reason.
        """
        if not '|' in token:
            raise ValueError(
                'Supposed client token "%s" does not contain a pipe.' % token
            )

        username, password = token.rsplit('|', 1)
        return self.decode_two_part(_db, username, password)
        
    def decode_two_part(self, _db, username, password):
        """Decode a short client token that has already been split into
        two parts.
        """
        signature = self.adobe_base64_decode(password)
        return self._decode(_db, username, signature)

    def _decode(self, _db, token, supposed_signature):
        """Make sure a client token is properly formatted, correctly signed,
        and not expired.
        """
        if token.count('|') < 2:
            raise ValueError("Invalid client token: %s" % token)
        library_short_name, expiration, patron_identifier = token.split("|", 2)

        library_short_name = library_short_name.upper()

        # Look up the Library object based on short name.
        library = get_one(_db, Library, adobe_short_name=library_short_name)
        if not library:
            raise ValueError(
                "I don't know how to handle tokens from library \"%s\"" % library_short_name
            )
        secret = library.adobe_shared_secret
        
        try:
            expiration = float(expiration)
        except ValueError:
            raise ValueError('Expiration time "%s" is not numeric.' % expiration)

        # We don't police the content of the patron identifier but there
        # has to be _something_ there.
        if not patron_identifier:
            raise ValueError(
                "Token %s has empty patron identifier." % token
            )

        # Don't bother checking an expired token.
        now = datetime.datetime.utcnow()
        expiration = self.EPOCH + datetime.timedelta(seconds=expiration)
        if expiration < now:
            raise ValueError(
                "Token %s expired at %s (now is %s)." % (
                    token, expiration, now
                )
            )

        # Sign the token and check against the provided signature.
        key = self.signer.prepare_key(secret)
        actual_signature = self.signer.sign(token, key)
        
        if actual_signature != supposed_signature:
            raise ValueError(
                "Invalid signature for %s." % token
            )

        # We have a Library, and a patron identifier which we know is valid.
        # Find or create a DelegatedPatronIdentifier for this person.
        delegated_patron_identifier, is_new = (
            DelegatedPatronIdentifier.get_one_or_create(
                _db, library, patron_identifier,
                DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID, self.uuid
            )
        )        
        return delegated_patron_identifier

class ExternalIntegration(Base):

    """An external integration contains configuration for connecting
    to a third-party API.
    """

    # Possible goals of ExternalIntegrations.

    # These integrations are associated with external services such as
    # Adobe Vendor ID, which manage access to DRM-dependent content.
    DRM_GOAL = u'drm'

    # Integrations with DRM_GOAL
    ADOBE_VENDOR_ID = u'Adobe Vendor ID'

    __tablename__ = 'externalintegrations'
    id = Column(Integer, primary_key=True)

    # Each integration should have a protocol (explaining what type of
    # code or network traffic we need to run to get things done) and a
    # goal (explaining the real-world goal of the integration).
    #
    # Basically, the protocol is the 'how' and the goal is the 'why'.
    protocol = Column(Unicode, nullable=False)
    goal = Column(Unicode, nullable=True)

    # A unique name for this ExternalIntegration. This is primarily
    # used to identify ExternalIntegrations from command-line scripts.
    name = Column(Unicode, nullable=True, unique=True)
    
    # Any additional configuration information goes into
    # ConfigurationSettings.
    settings = relationship(
        "ConfigurationSetting", backref="external_integration",
        lazy="joined", cascade="save-update, merge, delete, delete-orphan",
    )

    def __repr__(self):
        return u"<ExternalIntegration: protocol=%s goal='%s' settings=%d ID=%d>" % (
            self.protocol, self.goal, len(self.settings), self.id)

    @classmethod
    def lookup(cls, _db, protocol, goal):
        integrations = _db.query(cls).filter(
            cls.protocol==protocol, cls.goal==goal
        )

        integrations = integrations.all()
        if len(integrations) > 1:
            logging.warn("Multiple integrations found for '%s'/'%s'" % (protocol, goal))

        if not integrations:
            return None
        return integrations[0]

    def set_setting(self, key, value):
        """Create or update a key-value setting for this ExternalIntegration."""
        setting = self.setting(key)
        setting.value = value
        return setting
    
    def setting(self, key):
        """Find or create a ConfigurationSetting on this ExternalIntegration.

        :param key: Name of the setting.
        :return: A ConfigurationSetting
        """
        return ConfigurationSetting.for_externalintegration(
            key, self
        )

    def explain(self, include_secrets=False):
        """Create a series of human-readable strings to explain an
        ExternalIntegration's settings.

        :param include_secrets: For security reasons,
           sensitive settings such as passwords are not displayed by default.

        :return: A list of explanatory strings.
        """
        lines = []
        lines.append("ID: %s" % self.id)
        if self.name:
            lines.append("Name: %s" % self.name)
        lines.append("Protocol/Goal: %s/%s" % (self.protocol, self.goal))

        def key(setting):
            if setting.library:
                return setting.key, setting.library.name
            return (setting.key, None)
        for setting in sorted(self.settings, key=key):
            explanation = "%s='%s'" % (setting.key, setting.value)
            if setting.library:
                explanation = "%s (applies only to %s)" % (
                    explanation, setting.library.name
                )
            if include_secrets or not setting.is_secret:
                lines.append(explanation)
        return lines


class ConfigurationSetting(Base):
    """An extra piece of site configuration.

    A ConfigurationSetting may be associated with an
    ExternalIntegration, a Library, both, or neither.

    * The secret used by the circulation manager to sign OAuth bearer
      tokens is not associated with an ExternalIntegration or with a
      Library.

    * The link to a library's privacy policy is associated with the
      Library, but not with any particular ExternalIntegration.

    * The "website ID" for an Overdrive collection is associated with
      an ExternalIntegration (the Overdrive integration), but not with
      any particular Library (since multiple libraries might share an
      Overdrive collection).

    * The "identifier prefix" used to determine which library a patron
      is a patron of, is associated with both a Library and an
      ExternalIntegration.
    """
    __tablename__ = 'configurationsettings'
    id = Column(Integer, primary_key=True)
    external_integration_id = Column(
        Integer, ForeignKey('externalintegrations.id'), index=True
    )
    library_id = Column(
        Integer, ForeignKey('libraries.id'), index=True
    )
    key = Column(Unicode, index=True)
    _value = Column(Unicode, name="value")

    __table_args__ = (
        UniqueConstraint('external_integration_id', 'library_id', 'key'),
    )

    def __repr__(self):
        return u'<ConfigurationSetting: key=%s, ID=%d>' % (
            self.key, self.id)

    @classmethod
    def sitewide_secret(cls, _db, key):
        """Find or create a sitewide shared secret.

        The value of this setting doesn't matter, only that it's
        unique across the site and that it's always available.
        """
        secret = ConfigurationSetting.sitewide(_db, key)
        if not secret.value:
            secret.value = os.urandom(24).encode('hex')
            # Commit to get this in the database ASAP.
            _db.commit()
        return secret.value

    @classmethod
    def explain(cls, _db, include_secrets=False):
        """Explain all site-wide ConfigurationSettings."""
        lines = []
        site_wide_settings = []
        
        for setting in _db.query(ConfigurationSetting).filter(
                ConfigurationSetting.library_id==None).filter(
                    ConfigurationSetting.external_integration==None):
            if not include_secrets and setting.key.endswith("_secret"):
                continue
            site_wide_settings.append(setting)
        if site_wide_settings:
            lines.append("Site-wide configuration settings:")
            lines.append("---------------------------------")
        for setting in sorted(site_wide_settings, key=lambda s: s.key):
            lines.append("%s='%s'" % (setting.key, setting.value))
        return lines

    @classmethod
    def sitewide(cls, _db, key):
        """Find or create a sitewide ConfigurationSetting."""
        return cls.for_library_and_externalintegration(_db, key, None, None)

    @classmethod
    def for_library(cls, key, library):
        """Find or create a ConfigurationSetting for the given Library."""
        _db = Session.object_session(library)
        return cls.for_library_and_externalintegration(_db, key, library, None)

    @classmethod
    def for_externalintegration(cls, key, externalintegration):
        """Find or create a ConfigurationSetting for the given
        ExternalIntegration.
        """
        _db = Session.object_session(externalintegration)
        return cls.for_library_and_externalintegration(
            _db, key, None, externalintegration
        )
    
    @classmethod
    def for_library_and_externalintegration(
            cls, _db, key, library, external_integration
    ):
        """Find or create a ConfigurationSetting associated with a Library
        and an ExternalIntegration.
        """
        library_id = None
        if library:
            library_id = library.id
        setting, ignore = get_one_or_create(
            _db, ConfigurationSetting,
            library_id=library_id, external_integration=external_integration,
            key=key
        )
        return setting

    @property
    def library(self):
        _db = Session.object_session(self)
        if self.library_id:
            return get_one(_db, Library, id=self.library_id)
        return None

    @hybrid_property
    def value(self):
        """What's the current value of this configuration setting?
        
        If not present, the value may be inherited from some other
        ConfigurationSetting.
        """
        if self._value:
            # An explicitly set value always takes precedence.
            return self._value
        elif self.library_id and self.external_integration:
            # This is a library-specific specialization of an
            # ExternalIntegration. Treat the value set on the
            # ExternalIntegration as a default.
            return self.for_externalintegration(
                self.key, self.external_integration).value
        elif self.library_id:
            # This is a library-specific setting. Treat the site-wide
            # value as a default.
            _db = Session.object_session(self)
            return self.sitewide(_db, self.key).value
        return self._value
    
    @value.setter
    def set_value(self, new_value):
        self._value = new_value

    @classmethod
    def _is_secret(self, key):
        """Should the value of the given key be treated as secret?

        This will have to do, in the absence of programmatic ways of
        saying that a specific setting should be treated as secret.
        """
        return any(
            key == x or
            key.startswith('%s_' % x) or
            key.endswith('_%s' % x) or
            ("_%s_" %x) in key
            for x in ('secret', 'password')
        )

    @property
    def is_secret(self):
        """Should the value of this key be treated as secret?"""
        return self._is_secret(self.key)

    def value_or_default(self, default):
        """Return the value of this setting. If the value is None,
        set it to `default` and return that instead.
        """
        if self.value is None:
            self.value = default
        return self.value
    
    MEANS_YES = set(['true', 't', 'yes', 'y'])
    @property
    def bool_value(self):
        """Turn the value into a boolean if possible.

        :return: A boolean, or None if there is no value.
        """
        if self.value:
            if self.value.lower() in self.MEANS_YES:
                return True
            return False
        return None
        
    @property
    def int_value(self):
        """Turn the value into an int if possible.

        :return: An integer, or None if there is no value.

        :raise ValueError: If the value cannot be converted to an int.
        """
        if self.value:
            return int(self.value)
        return None

    @property
    def float_value(self):
        """Turn the value into an float if possible.

        :return: A float, or None if there is no value.

        :raise ValueError: If the value cannot be converted to a float.
        """
        if self.value:
            return float(self.value)
        return None
    
    @property
    def json_value(self):
        """Interpret the value as JSON if possible.

        :return: An object, or None if there is no value.

        :raise ValueError: If the value cannot be parsed as JSON.
        """
        if self.value:
            return json.loads(self.value)
        return None

