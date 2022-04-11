import json
import logging
import random
import re
import string
import uuid
import warnings
from collections import defaultdict
from datetime import datetime, timedelta

import uszipcode
from flask_babel import lazy_gettext as lgt
from flask_bcrypt import check_password_hash, generate_password_hash
from geoalchemy2 import Geography, Geometry
from sqlalchemy import (Boolean, Column, DateTime, Enum, ForeignKey, Index,
                        Integer, String, Table, Unicode, UniqueConstraint,
                        create_engine)
from sqlalchemy import exc as sa_exc
from sqlalchemy import func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import (aliased, backref, relationship, sessionmaker,
                            validates)
from sqlalchemy.orm.exc import MultipleResultsFound
from sqlalchemy.orm.session import Session
from sqlalchemy.sql.expression import (and_, cast, or_, select)

from library_registry.constants import (
    LibraryType,
    PLACE_CITY,
    PLACE_COUNTY,
    PLACE_EVERYWHERE,
    PLACE_LIBRARY_SERVICE_AREA,
    PLACE_NATION,
    PLACE_POSTAL_CODE,
    PLACE_STATE,
)
from library_registry.config import CannotSendEmail, Configuration
from library_registry.emailer import Emailer
from library_registry.model_helpers import (create, generate_secret, get_one, get_one_or_create)
from library_registry.util import GeometryUtility
from library_registry.util.language import LanguageCodes


DEBUG = False
Base = declarative_base()


def production_session():
    url = Configuration.database_url()
    logging.debug(f"Database url: {url}")
    _db = SessionManager.session(url)

    # The first thing to do after getting a database connection is to set up the logging configuration.
    #
    # If called during a unit test, this will configure logging incorrectly, but 1) this method isn't
    # normally called during unit tests, and 2) package_setup() will call initialize() again with the
    # right arguments.
    from library_registry.log import LogConfiguration
    LogConfiguration.initialize(_db)
    return _db


class SessionManager:
    ##### Class Constants ####################################################  # noqa: E266
    engine_for_url = {}

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    ##### Private Methods ####################################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

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

    ##### Private Class Methods ##############################################  # noqa: E266


class Library(Base):
    """
    Library Model
    """
    ##### Class Constants ####################################################  # noqa: E266
    TESTING_STAGE       = 'testing'     # Library should show up in test feed           # noqa: E221
    PRODUCTION_STAGE    = 'production'  # Library should show up in production feed     # noqa: E221
    CANCELLED_STAGE     = 'cancelled'   # Library should not show up in any feed        # noqa: E221
    PLS_ID              = "pls_id"      # Public Library Surveys ID                     # noqa: E221
    WHITESPACE_REGEX    = re.compile(r"\s+")                                            # noqa: E221

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    def set_hyperlink(self, rel, *hrefs):
        """
        Make sure Library has a Hyperlink with the given `rel` that points to a Resource with
        one of the given `href`s.

        If there's already a matching Hyperlink, it will be returned unmodified. Otherwise, the
        first item in `hrefs` will be used as the basis for a new Hyperlink, or an existing
        Hyperlink will be modified to use the first item in `hrefs` as its Resource.

        :return: A 2-tuple (Hyperlink, is_modified). `is_modified` is True if a new Hyperlink was
            created _or_ an existing Hyperlink was modified.
        """
        if not rel:
            raise ValueError("No link relation was specified")

        if not hrefs:
            raise ValueError("No Hyperlink hrefs were specified")

        default_href = hrefs[0]
        _db = Session.object_session(self)
        (hyperlink, is_modified) = get_one_or_create(_db, Hyperlink, library=self, rel=rel,)

        if hyperlink.href not in hrefs:
            hyperlink.href = default_href
            is_modified = True

        return hyperlink, is_modified

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = 'libraries'

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    stage_enum = Enum(TESTING_STAGE, PRODUCTION_STAGE, CANCELLED_STAGE, name='library_stage')

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)
    name = Column(Unicode, index=True)
    description = Column(Unicode)
    internal_urn = Column(Unicode, nullable=False, index=True, unique=True,
                          default=lambda: "urn:uuid:" + str(uuid.uuid4()))
    authentication_url = Column(Unicode, index=True)
    opds_url = Column(Unicode)
    web_url = Column(Unicode)
    timestamp = Column(DateTime, index=True, default=datetime.utcnow, onupdate=datetime.utcnow)
    logo = Column(Unicode)
    _library_stage = Column(stage_enum, index=True, nullable=False, default=TESTING_STAGE, name="library_stage")
    registry_stage = Column(stage_enum, index=True, nullable=False, default=TESTING_STAGE)
    anonymous_access = Column(Boolean, default=False)
    online_registration = Column(Boolean, default=False)
    short_name = Column(Unicode, index=True, unique=True)
    shared_secret = Column(Unicode)

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    aliases = relationship("LibraryAlias", backref='library')
    service_areas = relationship('ServiceArea', backref='library')
    audiences = relationship('Audience', secondary='libraries_audiences', back_populates="libraries")
    collections = relationship("CollectionSummary", backref='library')
    delegated_patron_identifiers = relationship("DelegatedPatronIdentifier", backref='library')
    hyperlinks = relationship("Hyperlink", backref='library')
    settings = relationship("ConfigurationSetting", backref="library", lazy="joined", cascade="all, delete")

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    @validates('short_name')
    def validate_short_name(self, key, value):
        if not value:
            return value
        if '|' in value:
            raise ValueError(
                'Short name cannot contain the pipe character.'
            )
        return value.upper()

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    @hybrid_property
    def library_stage(self):
        return self._library_stage

    @library_stage.setter
    def library_stage(self, value):
        """A library can't unilaterally go from being in production to not being in production"""
        if self.in_production and value != self.PRODUCTION_STAGE:
            msg = "This library is already in production; only the registry can take it out of production."
            raise ValueError(msg)

        self._library_stage = value

    @property
    def pls_id(self):
        return ConfigurationSetting.for_library(Library.PLS_ID, self)

    @property
    def number_of_patrons(self):
        db = Session.object_session(self)

        if not self.in_production:
            return 0  # Count is only meaningful if the library is in production

        query = db.query(DelegatedPatronIdentifier).filter(
            DelegatedPatronIdentifier.type == DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID,
            DelegatedPatronIdentifier.library_id == self.id
        )

        return query.count()

    @property
    def in_production(self):
        """Is this library in production? If library and registry agree on production, it is."""
        return bool(self.library_stage == self.PRODUCTION_STAGE and self.registry_stage == self.PRODUCTION_STAGE)

    @property
    def types(self):
        """
        Return any special types for this library.

        :yield: A sequence of code constants from LibraryTypes.
        """
        service_area = self.service_area
        if not service_area:
            return

        code = service_area.library_type

        if code:
            yield code

        # TODO: in the future, more types, e.g. audience-based, can go here.

    @property
    def service_area(self):
        """
        Return the service area of this Library, assuming there is only one.

        :return: A Place, if there is one well-defined place this library serves; otherwise None.
        """
        everywhere = None

        # Group the ServiceAreas by type.
        by_type = defaultdict(set)

        for a in self.service_areas:
            if not a.place:
                continue

            if a.place.type == Place.EVERYWHERE:
                # We will only return 'everywhere' if we don't find something more specific.
                everywhere = a.place
                continue

            by_type[a.type].add(a)

        # If there is a single focus area, use it. Otherwise, if there is a single eligibility area, use that.
        service_area = None

        for area_type in ServiceArea.FOCUS, ServiceArea.ELIGIBILITY:
            if len(by_type[area_type]) == 1:
                [service_area] = by_type[area_type]
                if service_area.place:
                    return service_area.place

        # This library serves everywhere, and it doesn't _also_ serve some more specific place.
        if everywhere:
            return everywhere

        # This library does not have one ServiceArea that stands out.
        return None

    @property
    def service_area_name(self):
        """
        Describe the library's service area in a short string a human would understand, e.g. "Kern County, CA".

        This library does the best it can to express a library's service area as the name of a single place,
        but it's not always possible since libraries can have multiple service areas.

        TODO: We'll want to fetch a library's ServiceAreas (and their Places) as part of the query that fetches
        libraries, so that this doesn't result in extra DB queries per library.

        :return: A string, or None if the library's service area can't be described as a short string.
        """
        if self.service_area:
            return self.service_area.human_friendly_name
        return None

    ##### Class Methods ######################################################  # noqa: E266

    @classmethod
    def for_short_name(cls, _db, short_name):
        """Look up a library by short name."""
        return get_one(_db, Library, short_name=short_name)

    @classmethod
    def for_urn(cls, _db, urn):
        """Look up a library by URN."""
        return get_one(_db, Library, internal_urn=urn)

    @classmethod
    def random_short_name(cls, duplicate_check=None, max_attempts=20):
        """
        Generate a random short name for a library.

        Library short names are six uppercase letters.

        :param duplicate_check: Call this function to check whether a generated name is a duplicate.
        :param max_attempts: Stop trying to generate a name after this many failures.
        """
        attempts = 0
        choice = None
        while not choice and attempts < max_attempts:
            choice = "".join([random.choice(string.ascii_uppercase) for i in range(6)])

            if callable(duplicate_check) and duplicate_check(choice):
                choice = None

            attempts += 1

        if choice is None:  # Something's wrong, need to raise an exception.
            raise ValueError(f"Could not generate random short name after {attempts} attempts!")

        return choice

    @classmethod
    def patron_counts_by_library(self, _db, libraries):
        """
        Determine the number of registered Adobe Account IDs (~patrons) for each of the given libraries.

        :param _db: A database connection.
        :param libraries: A list of Library objects.
        :return: A dictionary mapping library IDs to patron counts.
        """
        # The concept of 'patron count' only makes sense for production libraries.
        library_ids = [lib.id for lib in libraries if lib.in_production]

        # Run the SQL query.
        counts = select(
            [
                DelegatedPatronIdentifier.library_id,
                func.count(DelegatedPatronIdentifier.id)
            ],
        ).where(
            and_(DelegatedPatronIdentifier.type == DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID,
                 DelegatedPatronIdentifier.library_id.in_(library_ids))
        ).group_by(
            DelegatedPatronIdentifier.library_id
        ).select_from(
            DelegatedPatronIdentifier
        )
        rows = _db.execute(counts)

        # Convert the results to a dictionary.
        results = dict()
        for (library_id, count) in rows:
            results[library_id] = count

        return results

    @classmethod
    def nearby(cls, _db, target, max_radius=150, production=True):
        """Find libraries whose service areas include or are close to the
        given point.
        :param target: The starting point. May be a Geometry object or
         a 2-tuple (latitude, longitude).
        :param max_radius: How far out from the starting point to search
            for a library's service area, in kilometers.
        :param production: If True, only libraries that are ready for
            production are shown.
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
        min_distance = func.min(func.ST_DistanceSphere(target, Place.geometry))

        qu = _db.query(Library).join(Library.service_areas).join(
            ServiceArea.place)
        qu = qu.filter(cls._feed_restriction(production))
        qu = qu.filter(nearby)
        qu = qu.add_columns(
                min_distance).group_by(Library.id).order_by(
                min_distance.asc())
        return qu

    @classmethod
    def search(cls, _db, target, query, production=True):
        """Try as hard as possible to find a small number of libraries
        that match the given query.
        :param target: Order libraries by their distance from this
         point. May be a Geometry object or a 2-tuple (latitude,
         longitude).
        :param query: String to search for.
        :param production: If True, only libraries that are ready for
            production are shown.
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
                _db, library_query, here, production).limit(
                    max_libraries).all()
        else:
            libraries_for_name = []

        # We tack on any additional libraries that match a place query.
        if place_query:
            libraries_for_location = cls.search_by_location_name(
                _db, place_query, place_type, here, production
            ).limit(max_libraries).all()
        else:
            libraries_for_location = []

        if libraries_for_name and libraries_for_location:
            # Filter out any libraries that show up in both lists.
            for_name = set(libraries_for_name)
            libraries_for_location = [
                x for x in libraries_for_location if x not in for_name
            ]

        # A lot of libraries list their locations only within their description, so it's worth
        # checking the description for the search term.
        libraries_for_description = cls.search_within_description(
            _db, query, here, production
        ).limit(max_libraries).all()

        all_results = libraries_for_name + libraries_for_location + libraries_for_description
        unique_library_ids = []
        unique_results = []
        for library in all_results:
            # Sometimes a 'library' in the list is a Library instance, sometimes it's a 2-tuple,
            # of Library instance and distance.
            if isinstance(library, Library) and library.id not in unique_library_ids:
                unique_library_ids.append(library.id)
                unique_results.append(library)
            elif (
                    isinstance(library, tuple)
                    and isinstance(library[0], Library)
                    and library[0].id not in unique_library_ids
            ):
                unique_library_ids.append(library[0].id)
                unique_results.append(library)

        return unique_results

    @classmethod
    def search_by_library_name(cls, _db, name, here=None, production=True):
        """
        Find libraries whose name or alias matches the given name.

        :param name: Name of the library to search for.
        :param here: Order results by proximity to this location.
        :param production: If True, only libraries that are ready for production are shown.
        """
        name_matches = cls.fuzzy_match(Library.name, name)
        alias_matches = cls.fuzzy_match(LibraryAlias.name, name)
        partial_matches = cls.partial_match(Library.name, name)
        return cls.create_query(_db, here, production, name_matches, alias_matches, partial_matches)

    @classmethod
    def search_by_location_name(cls, _db, query, type=None, here=None, production=True):
        """
        Find libraries whose service area overlaps a place with the given name.

        :param query: Name of the place to search for.
        :param type: Restrict results to places of this type.
        :param here: Order results by proximity to this location.
        :param production: If True, only libraries that are ready for production are shown.
        """
        # For a library to match, the Place named by the query must intersect a Place served by that library.
        named_place = aliased(Place)
        qu = _db.query(Library).join(
            Library.service_areas).join(
                ServiceArea.place).join(
                    named_place,
                    func.ST_Intersects(Place.geometry, named_place.geometry)
                ).outerjoin(named_place.aliases)

        qu = qu.filter(cls._feed_restriction(production))
        name_match = cls.fuzzy_match(named_place.external_name, query)
        alias_match = cls.fuzzy_match(PlaceAlias.name, query)
        qu = qu.filter(or_(name_match, alias_match))

        if type:
            qu = qu.filter(named_place.type == type)

        if here:
            min_distance = func.min(func.ST_DistanceSphere(here, named_place.geometry))
            qu = qu.add_columns(min_distance)
            qu = qu.group_by(Library.id)
            qu = qu.order_by(min_distance.asc())

        return qu

    us_zip = re.compile("^[0-9]{5}$")
    us_zip_plus_4 = re.compile("^[0-9]{5}-[0-9]{4}$")
    running_whitespace = re.compile(r"\s+")

    @classmethod
    def create_query(cls, _db, here=None, production=True, *args):
        qu = _db.query(Library).outerjoin(Library.aliases)

        if here:
            qu = qu.outerjoin(Library.service_areas).outerjoin(ServiceArea.place)

        qu = qu.filter(or_(*args))
        qu = qu.filter(cls._feed_restriction(production))

        if here:
            # Order by the minimum distance between one of the
            # library's service areas and the current location.
            min_distance = func.min(func.ST_DistanceSphere(here, Place.geometry))
            qu = qu.add_columns(min_distance)
            qu = qu.group_by(Library.id)
            qu = qu.order_by(min_distance.asc())

        return qu

    @classmethod
    def search_within_description(cls, _db, query, here=None, production=True):
        """
        Find libraries whose descriptions include the search term.

        :param query: The string to search for.
        :param here: Order results by proximity to this location.
        :param production: If True, only libraries that are ready for production are shown.
        """
        description_matches = cls.fuzzy_match(Library.description, query)
        partial_matches = cls.partial_match(Library.description, query)
        return cls.create_query(_db, here, production, description_matches, partial_matches)

    @classmethod
    def query_cleanup(cls, query):
        """Clean up a query."""
        query = query.lower()
        query = cls.WHITESPACE_REGEX.sub(" ", query).strip()
        query = query.replace("libary", "library")  # Correct the most common misspelling of 'library'
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
        """
        Turn a query received by a user into a set of things to check against different bits of the database.
        """
        query = cls.query_cleanup(query)
        postal_code = cls.as_postal_code(query)

        if postal_code:
            # The query is a postal code. Don't even bother searching for a library name -- just find that code.
            return None, postal_code, Place.POSTAL_CODE

        # In theory, absolutely anything could be a library name or alias. We'll let Levenshtein distance
        # take care of minor typos, but we don't process the query very much before seeing if it matches a
        # library name.
        library_query = query

        # If the query looks like a library name, extract a location from it. This will find the public library
        # in Irvine from "irvine public library", even though there is no library called the "Irvine Public Library".
        #
        # NOTE: This will fall down if there is a place with "Library" in the name, but there are no such
        # places in the US.
        place_query = query
        place_type = None

        for indicator in 'public library', 'library':
            if indicator in place_query:
                place_query = place_query.replace(indicator, '').strip()

        (place_query, place_type) = Place.parse_name(place_query)

        return library_query, place_query, place_type

    @classmethod
    def fuzzy_match(cls, field, value):
        """
        Create a SQL clause that attempts a fuzzy match of the given field against the given value.

        If the field's value is less than six characters, we require an exact (case-insensitive) match.
        Otherwise, we require a Levenshtein distance of less than two between the field value and
        the provided value.
        """
        is_long = func.length(field) >= 6
        close_enough = func.levenshtein(func.lower(field), value) <= 2
        long_value_is_approximate_match = (is_long & close_enough)
        exact_match = field.ilike(value)
        return or_(long_value_is_approximate_match, exact_match)

    @classmethod
    def partial_match(cls, field, value):
        """
        Create a SQL clause that attempts to match a partial value--e.g. just one word of a library's
        name--against the given field.
        """
        return field.ilike("%{}%".format(value))

    @classmethod
    def get_hyperlink(cls, library, rel):
        link = [x for x in library.hyperlinks if x.rel == rel]
        if len(link) > 0:
            return link[0]

    ##### Private Class Methods ##############################################  # noqa: E266

    @classmethod
    def _feed_restriction(cls, production, library_field=None, registry_field=None):
        """
        Create a SQLAlchemy restriction that only finds libraries that ought to be in the given feed.

        :param production: A boolean. If True, then only libraries in the production stage should be included.
            If False, then libraries in the production or testing stages should be included.

        :return: A SQLAlchemy expression.
        """
        if library_field is None:
            library_field = Library.library_stage    # The library's opinion

        if registry_field is None:
            registry_field = Library.registry_stage  # The registry's opinion

        prod = cls.PRODUCTION_STAGE
        test = cls.TESTING_STAGE

        if production:      # Both parties must agree that this library is production-ready
            return and_(library_field == prod, registry_field == prod)
        else:               # Both must agree library is in _either_ prod stage or test stage
            return and_(library_field.in_((prod, test)), registry_field.in_((prod, test)))


class LibraryAlias(Base):
    """An alternate name for a library."""
    ##### Class Constants ####################################################  # noqa: E266

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = 'libraryalias'
    __table_args__ = (
        UniqueConstraint('library_id', 'name', 'language'),
    )

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)
    name = Column(Unicode, index=True)
    language = Column(Unicode(3), index=True)

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    library_id = Column(Integer, ForeignKey('libraries.id'), index=True)

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    ##### Private Class Methods ##############################################  # noqa: E266


class ServiceArea(Base):
    """
    Designates a geographic area served by a Library.

    A ServiceArea maps a Library to a Place. People living in this Place have service from the Library.
    """
    ##### Class Constants ####################################################  # noqa: E266

    # A library may have a ServiceArea because people in that area are eligible for service, or because
    # the library specifically focuses on that area.
    ELIGIBILITY = 'eligibility'
    FOCUS = 'focus'

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = 'serviceareas'
    __table_args__ = (
        UniqueConstraint('library_id', 'place_id', 'type'),
    )

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    servicearea_type_enum = Enum(ELIGIBILITY, FOCUS, name='servicearea_type')

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)
    type = Column(servicearea_type_enum, index=True, nullable=False, default=ELIGIBILITY)

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    library_id = Column(Integer, ForeignKey('libraries.id'), index=True)
    place_id = Column(Integer, ForeignKey('places.id'), index=True)

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    ##### Private Class Methods ##############################################  # noqa: E266


class Place(Base):
    """
    A location on the earth, with a defined geometry.

    Notes:
        * Regarding the place type constants, (NATION, CITY, etc.):
            * These are the kinds of places we keep track of. These are not supposed to be precise terms.
            * Each census-designated place is called a 'city', even if it's not a city in the legal sense.
            * Countries that call their top-level administrative divisions something other than 'states'
              can still use 'state' as their type.

    Place attributes/columns:

        id                  - Integer primary key

        type                - The Place type, typically drawn from class constants (NATION, CITY, etc.)

        external_id         - Unique ID given to this Place in the data source it was derived from

        external_name       - Name given to this Place by the data source it was derived from

        abbreviated_name    - Canonical abbreviation for this Place. Generally used only for nations and states.

        geometry            - The geography of the Place itself. Stored internally as a Geometry, which means we
                              have to cast to Geography when doing calculations that involve great circle distance.

    Place model relationships:

        Place
            parent          - The most convenient place that 'contains' this place. For most places the most
                              convenient parent will be a state. For states, the best parent will be a nation.
                              A nation has no parent; neither does 'everywhere'.
            children        - The Places which use this Place as parent.

        PlaceAlias

        ServiceArea
    """
    ##### Class Constants ####################################################  # noqa: E266
    NATION                  = PLACE_NATION                  # noqa: E221
    STATE                   = PLACE_STATE                   # noqa: E221
    COUNTY                  = PLACE_COUNTY                  # noqa: E221
    CITY                    = PLACE_CITY                    # noqa: E221
    POSTAL_CODE             = PLACE_POSTAL_CODE             # noqa: E221
    LIBRARY_SERVICE_AREA    = PLACE_LIBRARY_SERVICE_AREA    # noqa: E221
    EVERYWHERE              = PLACE_EVERYWHERE              # noqa: E221

    ##### Public Interface / Magic Methods ###################################  # noqa: E266
    def __repr__(self):
        parent = self.parent.external_name if self.parent else None
        abbr = f"abbr={self.abbreviated_name} " if self.abbreviated_name else ''
        return f"<Place: {self.external_name} type={self.type} {abbr}external_id={self.external_id} parent={parent}>"

    def as_centroid_point(self):
        if not self.geometry:
            return None

        db_session = Session.object_session(self)
        centroid = func.ST_AsEWKT(func.ST_Centroid(Place.geometry))
        stmt = select([centroid]).where(Place.id == self.id)
        return db_session.execute(stmt).scalar()

    def overlaps_not_counting_border(self, qu):
        """
        Modifies a filter to find places that have points inside this Place, not counting the border.

        Connecticut has no points inside New York, but the two states share a border. This method
        creates a more real-world notion of 'inside' that does not count a shared border.
        """
        intersects = Place.geometry.intersects(self.geometry)
        touches = func.ST_Touches(Place.geometry, self.geometry)
        return qu.filter(intersects).filter(touches == False)       # noqa: E712

    def lookup_inside(self, name, using_overlap=False, using_external_source=True):
        """
        Look up a named Place that is geographically 'inside' this Place.

        :param name: The name of a place, such as "Boston" or "Calabasas, CA", or "Cook County".

        :param using_overlap: If this is true, then place A is 'inside' place B if their shapes overlap,
            not counting borders. For example, Montgomery is 'inside' Montgomery County, Alabama, and
            the United States. However, Alabama is not 'inside' Georgia (even though they share a border).

            If `using_overlap` is false, then place A is 'inside' place B only if B is the .parent of A.
            In this case, Alabama is considered to be 'inside' the United States, but Montgomery is not
            -- the only place it's 'inside' is Alabama. Checking this way is much faster, so it's the default.

        :param using_external_source: If this is True, then if no named place can be found in the database,
            the uszipcodes library will be used in an attempt to find some equivalent postal codes.

        :return: A Place object, or None if no match could be found.

        :raise MultipleResultsFound: If more than one Place with the given name is 'inside' this Place.
        """
        parts = Place.name_parts(name)
        if len(parts) > 1:
            # We're trying to look up a scoped name such as "Boston, MA". `name_parts` has turned
            # "Boston, MA" into ["MA", "Boston"].
            #
            # Now we need to look for "MA" inside ourselves, and then look for "Boston" inside the object we get back.
            look_in_here = self
            for part in parts:
                look_in_here = look_in_here.lookup_inside(part, using_overlap)
                if not look_in_here:
                    # A link in the chain has failed. Return None
                    # immediately.
                    return None
            # Every link in the chain has succeeded, and `must_be_inside`
            # now contains the Place we were looking for.
            return look_in_here

        # If we get here, it means we're looking up "Boston" within Massachusetts, or "Kern County"
        # within the United States. In other words, we expect to find at most one place with
        # this name inside the `must_be_inside` object.
        #
        # If we find more than one, it's an error. The name should have been scoped better. This will
        # happen if you search for "Springfield" or "Lake County" within the United States, instead of
        # specifying which state you're talking about.
        _db = Session.object_session(self)
        qu = Place.lookup_by_name(_db, name).filter(Place.type != self.type)

        # Don't look in a place type known to be 'bigger' than this place.
        exclude_types = Place.larger_place_types(self.type)
        qu = qu.filter(~Place.type.in_(exclude_types))

        if self.type == self.EVERYWHERE:
            # The concept of 'inside' is not relevant because every place is 'inside' EVERYWHERE.
            # We are really trying to find one and only one place with a certain name.
            pass
        else:
            if using_overlap and self.geometry is not None:
                qu = self.overlaps_not_counting_border(qu)
            else:
                parent = aliased(Place)
                grandparent = aliased(Place)
                qu = qu.join(parent, Place.parent_id == parent.id)
                qu = qu.outerjoin(grandparent, parent.parent_id == grandparent.id)

                # For postal codes, but no other types of places, we allow the lookup to skip a level.
                # This lets you look up "93203" within a state *or* within the nation.
                postal_code_grandparent_match = and_(Place.type == Place.POSTAL_CODE, grandparent.id == self.id)
                qu = qu.filter(or_(Place.parent == self, postal_code_grandparent_match))

        places = qu.all()
        if len(places) == 0:
            if using_external_source:
                # We don't have any matching places in the database _now_, but there's a possibility
                # we can find a representative postal code.
                return self.lookup_one_through_external_source(name)
            else:
                # We're not allowed to use uszipcodes, probably because this method was called by
                # lookup_through_external_source.
                return None
        if len(places) > 1:
            raise MultipleResultsFound(f"More than one place called {name} inside {self.external_name}.")
        return places[0]

    def lookup_one_through_external_source(self, name):
        """
        Use an external source to find a Place that is a) inside `self`
        and b) identifies the place human beings call `name`.

        Currently the only way this might work is when using
        uszipcodes to look up a city inside a state. In this case the result
        will be a Place representing one of the city's postal codes.

        :return: A Place, or None if the lookup fails.
        """
        if self.type != Place.STATE:
            return None         # uszipcodes keeps track of places in terms of their state.

        search = uszipcode.SearchEngine(
            db_file_dir=Configuration.DATADIR,
            simple_zipcode=True
        )
        state = self.abbreviated_name
        uszipcode_matches = []
        if (state in search.state_to_city_mapper and name in search.state_to_city_mapper[state]):
            # The given name is an exact match for one of the
            # cities. Let's look up every ZIP code for that city.
            uszipcode_matches = search.by_city_and_state(name, state, returns=None)

        # Look up a Place object for each ZIP code and return the
        # first one we actually know about.
        #
        # Set using_external_source to False to eliminate the
        # possibility of wasted effort or (I don't think this can
        # happen) infinite recursion.
        for match in uszipcode_matches:
            place = self.lookup_inside(match.zipcode, using_external_source=False)
            if place:
                return place

    def served_by(self):
        """
        Find all Libraries with a ServiceArea whose Place overlaps this Place, not counting the border.

        A Library whose ServiceArea borders this place, but does not intersect this place, is not counted.
        This way, the state library from the next state over doesn't count as serving your state.
        """
        _db = Session.object_session(self)
        qu = _db.query(Library).join(Library.service_areas).join(ServiceArea.place)
        qu = self.overlaps_not_counting_border(qu)
        return qu

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = "places"

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)
    type = Column(Unicode(255), index=True, nullable=False)
    external_id = Column(Unicode, index=True)
    external_name = Column(Unicode, index=True)
    abbreviated_name = Column(Unicode, index=True)
    geometry = Column(Geometry(srid=4326), nullable=True)

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    parent_id = Column(Integer, ForeignKey('places.id'), index=True)
    children = relationship("Place", backref=backref("parent", remote_side=[id]), lazy="joined")
    aliases = relationship("PlaceAlias", backref='place')
    service_areas = relationship("ServiceArea", backref="place")

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    @property
    def library_type(self):
        """
        If a library serves this place, what type of library does that make it?

        :return: A string; one of the constants from LibraryType.
        """
        if self.type == Place.EVERYWHERE:
            return LibraryType.UNIVERSAL
        elif self.type == Place.NATION:
            return LibraryType.NATIONAL
        elif self.type == Place.STATE:
            # Whether this is a 'state' library, 'province' library,
            # etc. depends on which nation it's in.
            library_type = LibraryType.STATE
            if self.parent and self.parent.type == Place.NATION:
                library_type = LibraryType.ADMINISTRATIVE_DIVISION_TYPES.get(
                    self.parent.abbreviated_name, library_type
                )
            return library_type
        elif self.type == Place.COUNTY:
            return LibraryType.COUNTY

        return LibraryType.LOCAL

    @property
    def hierarchy(self):
        """
        Returns a list of Place instances representing the parent, parent's parent, etc.
        """
        parents = []
        if self.parent:
            current_node = self.parent
            while current_node:
                parents.append(current_node)
                current_node = current_node.parent

        return parents

    @property
    def human_friendly_name(self):
        """
        Generate the sort of string a human would recognize as an unambiguous name for this place.
        This is in some sense the opposite of parse_name.

        Rules:
            1. If the place is EVERYWHERE, return None
            2. If the place has no parent, return its external_name
            3. If the place is a COUNTY with a STATE somewhere in its parentage, return a string concatenated from:
                - self.external_name,
                - the string ' County, ' or ' Parish, ' as appropriate
                - the abbreviated name of the STATE ancestor if defined, or its external name
            4. If the place is a CITY with a STATE somewhere in its parentage, return a string concatenated from:
                - self.external_name,
                - the string ', '
                - the abbreviated name of the STATE ancestor if defined, or its external name
            5. In all other cases return the external_name of this place instance

        :return: A string, or None if there is no human-friendly name for this place.
        """
        if self.type == self.EVERYWHERE:
            return None     # 'everywhere' is not a distinct place with a well-known name.

        if not self.parent:
            return self.external_name

        if (
            self.type in (self.COUNTY, self.CITY)
            and any([bool(x.type == Place.STATE) for x in self.hierarchy])
        ):
            [state_ancestor] = [p for p in self.hierarchy if p.type == Place.STATE]
            state_name = state_ancestor.abbreviated_name or state_ancestor.external_name
            county_word = 'County'

            if state_name.lower() in ['la', 'louisiana']:           # account for Louisiana
                county_word = 'Parish'

            if (
                self.type == Place.CITY
                or county_word.lower() in self.external_name.lower()   # don't do DeKalb County County, GA
            ):
                return f"{self.external_name}, {state_name}"
            else:
                return f"{self.external_name} {county_word}, {state_name}"

        # All other cases:
        #  93203
        #  Texas
        #  France
        return self.external_name

    ##### Class Methods ######################################################  # noqa: E266
    @classmethod
    def everywhere(cls, _db):
        """
        Return a special Place that represents everywhere.

        This place has no .geometry, so attempts to use it in geographic comparisons will fail.
        """
        (place, _) = get_one_or_create(
            _db, Place, type=cls.EVERYWHERE,
            create_method_kwargs={"external_id": "Everywhere", "external_name": "Everywhere"}
        )
        return place

    @classmethod
    def default_nation(cls, _db):
        """
        Return the default nation for this library registry.

        If an incoming coverage area doesn't mention a nation, we'll assume it's within this nation.

        :return: The default nation, if one can be found. Otherwise, None.
        """
        default_nation = None
        abbreviation = ConfigurationSetting.sitewide(_db, Configuration.DEFAULT_NATION_ABBREVIATION).value

        if abbreviation:
            default_nation = get_one(_db, Place, type=Place.NATION, abbreviated_name=abbreviation)

            if not default_nation:
                logging.error(f"Could not look up default nation {abbreviation}")

        return default_nation

    @classmethod
    def larger_place_types(cls, type):
        """
        Return a list of place types known to be bigger than `type`.

        Places don't form a strict heirarchy. In particular, ZIP codes are not 'smaller' than cities.
        But counties and cities are smaller than states, and states are smaller than nations, so
        if you're searching inside a state for a place called "Japan", you know that the nation of
        Japan is not what you're looking for.
        """
        larger = [Place.EVERYWHERE]
        if type not in (Place.NATION, Place.EVERYWHERE):
            larger.append(Place.NATION)
        if type in (Place.COUNTY, Place.CITY, Place.POSTAL_CODE):
            larger.append(Place.STATE)
        if type == Place.CITY:
            larger.append(Place.COUNTY)
        return larger

    @classmethod
    def parse_name(cls, place_name):
        """
        Try to extract a place type from a name.

        :return: A 2-tuple (place_name, place_type)

        e.g. "Kern County" becomes ("Kern", Place.COUNTY); "Arizona State" becomes ("Arizona", Place.STATE);
            "Chicago" becaomes ("Chicago", None)
        """
        check = place_name.lower()
        place_type = None
        if check.endswith(' county'):
            place_name = place_name[:-7]
            place_type = Place.COUNTY

        if check.endswith(' state'):
            place_name = place_name[:-6]
            place_type = Place.STATE
        return place_name, place_type

    @classmethod
    def lookup_by_name(cls, _db, name, place_type=None):
        """Look up one or more Places by name"""
        if not place_type:
            name, place_type = cls.parse_name(name)

        qu = _db.query(Place).outerjoin(PlaceAlias).filter(
            or_(Place.external_name == name, Place.abbreviated_name == name, PlaceAlias.name == name)
        )

        if place_type:
            qu = qu.filter(Place.type == place_type)
        else:
            # The place type "county" is excluded unless it was explicitly asked for (e.g. "Cook County").
            # This is to avoid ambiguity in the many cases when a state contains a county and a city with
            # the same name. In all realistic cases, someone using "Foo" to talk about a library service area
            # is referring to the city of Foo, not Foo County -- if they want Foo County they can say "Foo County".
            qu = qu.filter(Place.type != Place.COUNTY)

        return qu

    @classmethod
    def lookup_one_by_name(cls, _db, name, place_type=None):
        return cls.lookup_by_name(_db, name, place_type).one()

    @classmethod
    def to_geojson(cls, _db, *places):
        """Convert 1+ Place objects to a dict that will become a GeoJSON document when converted to JSON"""
        geojson = select(
            [func.ST_AsGeoJSON(Place.geometry)]
        ).where(
            Place.id.in_([x.id for x in places])
        )
        results = [x[0] for x in _db.execute(geojson)]
        if len(results) == 1:
            # There's only one item, and it is a valid GeoJSON document on its own.
            return json.loads(results[0])

        # We have either more or less than one valid item. In either case, a GeometryCollection is appropriate.
        body = {"type": "GeometryCollection", "geometries": [json.loads(x) for x in results]}
        return body

    @classmethod
    def name_parts(cls, name):
        """
        Split a nested geographic name into parts.

        "Boston, MA" is split into ["MA", "Boston"]
        "Lake County, Ohio, USA" is split into ["USA", "Ohio", "Lake County"]

        There is no guarantee that these place names correspond to Places in the database.

        :param name: The name to split into parts.
        :return: A list of place names, with the largest place at the front of the list.
        """
        return [x.strip() for x in reversed(name.split(",")) if x.strip()]


class PlaceAlias(Base):
    """An alternate name for a place."""

    ##### Class Constants ####################################################  # noqa: E266

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = 'placealiases'
    __table_args__ = (
        UniqueConstraint('place_id', 'name', 'language'),
    )

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)
    name = Column(Unicode, index=True)
    language = Column(Unicode(3), index=True)

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    place_id = Column(Integer, ForeignKey('places.id'), index=True)

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    ##### Private Class Methods ##############################################  # noqa: E266


class Audience(Base):
    """A class of person served by a library."""
    ##### Class Constants ####################################################  # noqa: E266

    PUBLIC = "public"                                   # The general public
    EDUCATIONAL_PRIMARY = "educational-primary"         # Pre-university students
    EDUCATIONAL_SECONDARY = "educational-secondary"     # University students
    RESEARCH = "research"                               # Academics and researchers
    PRINT_DISABILITY = "print-disability"               # People with print disabilities
    OTHER = "other"                                     # A catch-all for other specialized audiences.

    KNOWN_AUDIENCES = [
        EDUCATIONAL_PRIMARY,
        EDUCATIONAL_SECONDARY,
        OTHER,
        PRINT_DISABILITY,
        PUBLIC,
        RESEARCH,
    ]

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = 'audiences'

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)
    name = Column(Unicode, index=True, unique=True)

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    libraries = relationship("Library", secondary='libraries_audiences', back_populates="audiences")

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    @classmethod
    def lookup(cls, _db, name):
        if name not in cls.KNOWN_AUDIENCES:
            raise ValueError(lgt("Unknown audience: %(name)s", name=name))

        (audience, _) = get_one_or_create(_db, Audience, name=name)

        return audience

    ##### Private Class Methods ##############################################  # noqa: E266


class CollectionSummary(Base):
    """
    A summary of a collection held by a library.

    We only need to know the language of the collection and approximately how big it is.
    """
    ##### Class Constants ####################################################  # noqa: E266

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = 'collectionsummaries'

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)
    language = Column(Unicode)
    size = Column(Integer)

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    library_id = Column(Integer, ForeignKey('libraries.id'), index=True)

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    @classmethod
    def set(cls, library, language, size):
        """
        Create or update a CollectionSummary for the given library and language.

        :return: An up-to-date CollectionSummary.
        """
        _db = Session.object_session(library)

        try:
            size = int(float(size))
        except (ValueError, TypeError):
            raise ValueError(lgt("Collection size must be numeric"))

        if size < 0:
            raise ValueError(lgt("Collection size cannot be negative."))

        # This might return None, which is fine. We'll store it as a collection with an unknown language.
        # This also covers the case where the library specifies its collection size but doesn't mention any languages.
        language_code = LanguageCodes.string_to_alpha_3(language)

        (summary, _) = get_one_or_create(_db, CollectionSummary, library=library, language=language_code)
        summary.size = size

        return summary

    ##### Private Class Methods ##############################################  # noqa: E266


Index("ix_collectionsummary_language_size", CollectionSummary.language, CollectionSummary.size)


class Hyperlink(Base):
    """
    A link between a Library and a Resource.

    We trust that the Resource is actually associated with the Library because the library told us about it;
    either directly, during registration, or by putting a link in its Authentication For OPDS document.
    """
    ##### Class Constants ####################################################  # noqa: E266

    INTEGRATION_CONTACT_REL = "http://librarysimplified.org/rel/integration-contact"
    COPYRIGHT_DESIGNATED_AGENT_REL = "http://librarysimplified.org/rel/designated-agent/copyright"
    HELP_REL = "help"

    # Descriptions of the link relations, used in emails.
    REL_DESCRIPTIONS = {
        INTEGRATION_CONTACT_REL: "integration point of contact",
        COPYRIGHT_DESIGNATED_AGENT_REL: "copyright designated agent",
        HELP_REL: "patron help contact address",
    }

    # Hyperlinks with these relations are not for public consumption.
    PRIVATE_RELS = [INTEGRATION_CONTACT_REL]

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    def notify(self, emailer, url_for):
        """
        Notify the target of this hyperlink that it is, in fact, a target of the hyperlink.

        If the underlying resource needs a new validation, an ADDRESS_NEEDS_CONFIRMATION email will be sent,
        asking the person on the other end to confirm the address. Otherwise, an ADDRESS_DESIGNATED email will
        be sent, informing the person on the other end that their (probably already validated) email address
        was associated with another library.

        :param emailer: An Emailer, for sending out the email.
        :param url_for: An implementation of Flask's url_for, used to generate a validation link if necessary.
        """
        # Early exit conditions
        if (
            not isinstance(emailer, Emailer)                # Not passed a valid Emailer
            or not callable(url_for)                        # Not passed a callable url_for
            or not self.resource                            # Link not related to a Resource
            or not self.library                             # Link not related to a Library
            or not isinstance(self.resource, Resource)      # Link resource somehow not a Resource
            or not isinstance(self.library, Library)        # Link library somehow not a Library
        ):
            return

        _db = Session.object_session(self)
        registry_contact_email = ConfigurationSetting.sitewide(_db, Configuration.REGISTRY_CONTACT_EMAIL).value
        email_type = Emailer.ADDRESS_DESIGNATED     # Default to an informative email with no validation link.
        to_address = self.resource.href

        if to_address.startswith('mailto:'):
            to_address = to_address[7:]

        # Make sure there's a Validation object associated with this Resource.
        if self.resource.validation is None:
            (self.resource.validation, validation_is_new) = create(_db, Validation)
        else:
            validation_is_new = False

        if validation_is_new or not self.resource.validation.active:
            # Either this Validation was just created or it expired before being verified. Restart the
            # validation process and send an email that includes a validation link.
            email_type = Emailer.ADDRESS_NEEDS_CONFIRMATION
            self.resource.validation.restart()

        # Create values for all the variables expected by the default templates.
        template_args = {
            "rel_desc": Hyperlink.REL_DESCRIPTIONS.get(self.rel, self.rel),
            "library": self.library.name,
            "library_web_url": self.library.web_url,
            "email": to_address,
            "registry_support": registry_contact_email,
        }

        if email_type == Emailer.ADDRESS_NEEDS_CONFIRMATION:
            template_args['confirmation_link'] = url_for("libr.confirm_resource", resource_id=self.resource.id,
                                                         secret=self.resource.validation.secret)

        try:
            body = emailer.send(email_type, to_address, **template_args)
        except CannotSendEmail as exc:
            logging.error(str(exc))
            raise exc

        return body

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = 'hyperlinks'

    # A Library can have multiple links with the same rel, but we only need to keep track of one.
    __table_args__ = (
        UniqueConstraint('library_id', 'rel'),
    )

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)
    rel = Column(Unicode, index=True, nullable=False)

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    library_id = Column(Integer, ForeignKey('libraries.id'), index=True)
    resource_id = Column(Integer, ForeignKey('resources.id'), index=True)

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    @hybrid_property
    def href(self):
        if not self.resource:
            return None

        return self.resource.href

    @href.setter
    def href(self, url):
        _db = Session.object_session(self)
        (resource, _) = get_one_or_create(_db, Resource, href=url)
        self.resource = resource

    ##### Class Methods ######################################################  # noqa: E266

    ##### Private Class Methods ##############################################  # noqa: E266


class Resource(Base):
    """
    A URI, potentially linked to multiple libraries, or to a single library through multiple relationships.

    e.g. a library consortium may use a single email address as the patron help address and the integration
    contact address for all of its libraries. That address only needs to be validated once.
    """
    ##### Class Constants ####################################################  # noqa: E266

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    def restart_validation(self):
        """Start or restart the validation process for this resource."""
        if not self.validation:
            _db = Session.object_session(self)
            (self.validation, _) = create(_db, Validation)

        self.validation.restart()

        return self.validation

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = 'resources'

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)
    href = Column(Unicode, nullable=False, index=True, unique=True)

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    hyperlinks = relationship("Hyperlink", backref="resource")

    # Every Resource may have at most one Validation. There's no need to validate it separately for every relationship.
    validation_id = Column(Integer, ForeignKey('validations.id'), index=True)

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    ##### Private Class Methods ##############################################  # noqa: E266


class Validation(Base):
    """An attempt (successful, in-progress, or failed) to validate a Resource."""

    ##### Class Constants ####################################################  # noqa: E266

    # Used in OPDS catalogs to convey the status of a validation attempt.
    STATUS_PROPERTY = "https://schema.org/reservationStatus"

    # These constants are used in OPDS catalogs as values of schema:reservationStatus.
    CONFIRMED = "https://schema.org/ReservationConfirmed"
    IN_PROGRESS = "https://schema.org/ReservationPending"
    INACTIVE = "https://schema.org/ReservationCancelled"

    EXPIRES_AFTER = timedelta(days=1)

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    def restart(self):
        """
        Start a new validation attempt, cancelling any previous attempt.

        This does not send out a validation email -- that needs to be handled separately by something
        capable of generating the URL to the validation controller.
        """
        self.started_at = datetime.utcnow()
        self.secret = generate_secret()
        self.success = False

    def mark_as_successful(self):
        """Register the fact that the validation attempt has succeeded."""
        if self.success:
            raise Exception("This validation has already succeeded.")

        if not self.active:
            raise Exception("This validation has expired.")

        self.secret = None
        self.success = True

        # TODO: This may cause one or more libraries to switch from
        # "not completely validated" to "completely validated".

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = 'validations'

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)
    success = Column(Boolean, index=True, default=False)
    started_at = Column(DateTime, index=True, nullable=False, default=datetime.utcnow)

    # The only way to validate a Resource is to prove you know the corresponding secret.
    secret = Column(Unicode, default=generate_secret, unique=True)

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    resource = relationship("Resource", backref=backref("validation", uselist=False), uselist=False)

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    @property
    def deadline(self):
        if self.success:
            return None
        return self.started_at + self.EXPIRES_AFTER

    @property
    def active(self):
        """
        Is this Validation still active?

        An inactive Validation can't be marked as successful -- it needs to be reset.
        """
        now = datetime.utcnow()
        return not self.success and now < self.deadline

    ##### Class Methods ######################################################  # noqa: E266

    ##### Private Class Methods ##############################################  # noqa: E266


class DelegatedPatronIdentifier(Base):
    """
    An identifier generated by the library registry which identifies a patron of one of the libraries.

    This is probably an Adobe Account ID.
    """
    ##### Class Constants ####################################################  # noqa: E266

    ADOBE_ACCOUNT_ID = 'Adobe Account ID'

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = 'delegatedpatronidentifiers'

    __table_args__ = (
        UniqueConstraint('type', 'library_id', 'patron_identifier'),
    )

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)
    type = Column(String(255), index=True)

    # This is the ID the foreign library gives us when referring to this patron.
    patron_identifier = Column(String(255), index=True)

    # This is the identifier we made up for the patron. This is what the foreign library is trying to look up.
    delegated_identifier = Column(String)

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    library_id = Column(Integer, ForeignKey('libraries.id'), index=True)

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    @classmethod
    def get_one_or_create(cls, _db, library, patron_identifier, identifier_type, identifier_or_identifier_factory):
        """
        Look up the delegated identifier for the given patron. If there is none, create one.

        :param library: The Library in charge of the patron's record.

        :param patron_identifier: An identifier used by that library to distinguish between this patron and others.
            This should be an identifier created solely for the purpose of identifying the patron with the library
            registry, and not (e.g.) the patron's barcode.

        :param identifier_type: The type of the delegated identifier to look up. (probably ADOBE_ACCOUNT_ID)

        :param identifier_or_identifier_factory: If this patron does not have a DelegatedPatronIdentifier, one will
            be created, and this object will be used to set its .delegated_identifier. If a string is passed in,
            .delegated_identifier will be that string. If a function is passed in, .delegated_identifier will be set
            to the return value of the function call.

        :return: A 2-tuple (DelegatedPatronIdentifier, is_new)
        """
        (identifier, is_new) = get_one_or_create(
            _db, DelegatedPatronIdentifier, library=library,
            patron_identifier=patron_identifier, type=identifier_type
        )

        if is_new:
            if callable(identifier_or_identifier_factory):
                # We are in charge of creating the delegated identifier.
                delegated_identifier = identifier_or_identifier_factory()
            else:
                # We haven't heard of this patron before, but some other server does know about them,
                # and they told us this is the delegated identifier.
                delegated_identifier = identifier_or_identifier_factory

            identifier.delegated_identifier = delegated_identifier

        return identifier, is_new

    ##### Private Class Methods ##############################################  # noqa: E266


class ExternalIntegration(Base):
    """
    An external integration contains configuration for connecting to a third-party API.
    """
    ##### Class Constants ####################################################  # noqa: E266

    # Possible goals of ExternalIntegrations.

    # These integrations are associated with external services such as
    # Adobe Vendor ID, which manage access to DRM-dependent content.
    DRM_GOAL = 'drm'

    # Integrations with DRM_GOAL
    ADOBE_VENDOR_ID = 'Adobe Vendor ID'

    # These integrations are associated with external services that collect logs of server-side events.
    LOGGING_GOAL = 'logging'

    # Integrations with LOGGING_GOAL
    INTERNAL_LOGGING = 'Internal logging'
    LOGGLY = 'Loggly'

    # These integrations are for sending email.
    EMAIL_GOAL = 'email'

    # Integrations with EMAIL_GOAL
    SMTP = 'SMTP'

    # If there is a special URL to use for access to this API, put it here.
    URL = "url"

    # If access requires authentication, these settings represent the username/password or key/secret
    # combination necessary to authenticate. If there's a secret but no key, it's stored in 'password'.
    USERNAME = "username"
    PASSWORD = "password"

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    def __repr__(self):
        return (
            "<ExternalIntegration: "
            f"protocol={self.protocol} "
            f"goal='{self.goal}' "
            f"settings={len(self.settings)} "
            f"ID={self.id}>"
        )

    def set_setting(self, key, value):
        """Create or update a key-value setting for this ExternalIntegration."""
        setting = self.setting(key)
        setting.value = value
        return setting

    def setting(self, key):
        """
        Find or create a ConfigurationSetting on this ExternalIntegration.

        :param key: Name of the setting.
        :return: A ConfigurationSetting
        """
        return ConfigurationSetting.for_externalintegration(key, self)

    def explain(self, include_secrets=False):
        """
        Create a series of human-readable strings to explain an ExternalIntegration's settings.

        :param include_secrets: For security reasons, settings such as passwords are not displayed by default.

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
                explanation = "%s (applies only to %s)" % (explanation, setting.library.name)

            if include_secrets or not setting.is_secret:
                lines.append(explanation)

        return lines

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = 'externalintegrations'

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)

    # Each integration should have a protocol (explaining what type of code or network traffic we need to
    # run to get things done) and a goal (explaining the real-world goal of the integration).
    #
    # Basically, the protocol is the 'how' and the goal is the 'why'.
    protocol = Column(Unicode, nullable=False)
    goal = Column(Unicode, nullable=True)

    # A unique name for this ExternalIntegration. This is primarily used to identify ExternalIntegrations
    # from command-line scripts.
    name = Column(Unicode, nullable=True, unique=True)

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    # Any additional configuration information goes into ConfigurationSettings.
    settings = relationship(
        "ConfigurationSetting", backref="external_integration",
        lazy="joined", cascade="save-update, merge, delete, delete-orphan",
    )

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    @hybrid_property
    def url(self):
        return self.setting(self.URL).value

    @url.setter
    def url(self, new_url):
        self.set_setting(self.URL, new_url)

    @hybrid_property
    def username(self):
        return self.setting(self.USERNAME).value

    @username.setter
    def username(self, new_username):
        self.set_setting(self.USERNAME, new_username)

    @hybrid_property
    def password(self):
        return self.setting(self.PASSWORD).value

    @password.setter
    def password(self, new_password):
        return self.set_setting(self.PASSWORD, new_password)

    ##### Class Methods ######################################################  # noqa: E266

    @classmethod
    def lookup(cls, _db, protocol, goal):
        integrations = _db.query(cls).filter(cls.protocol == protocol, cls.goal == goal)

        integrations = integrations.all()

        if len(integrations) > 1:
            logging.warning("Multiple integrations found for '%s'/'%s'" % (protocol, goal))

        if not integrations:
            return None

        return integrations[0]

    ##### Private Class Methods ##############################################  # noqa: E266


class ConfigurationSetting(Base):
    """
    An extra piece of site configuration.

    A ConfigurationSetting may be associated with an ExternalIntegration, a Library, both, or neither.

    * The secret used by the circulation manager to sign OAuth bearer tokens is not associated with an
      ExternalIntegration or with a Library.

    * The link to a library's privacy policy is associated with the Library, but not with any particular
      ExternalIntegration.

    * The "website ID" for an Overdrive collection is associated with an ExternalIntegration (the
      Overdrive integration), but not with any particular Library (since multiple libraries might share
      an Overdrive collection).

    * The "identifier prefix" used to determine which library a patron is a patron of, is associated with
      both a Library and an ExternalIntegration.
    """
    ##### Class Constants ####################################################  # noqa: E266

    MEANS_YES = set(['true', 't', 'yes', 'y'])
    SECRET_SETTING_KEYWORDS = set(['password', 'secret'])

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    def __repr__(self):
        return '<ConfigurationSetting: key=%s, ID=%d>' % (self.key, self.id)

    def setdefault(self, default=None):
        """If no value is set, set it to `default`. Then return the current value."""
        if self.value is None:
            self.value = default

        return self.value

    def value_or_default(self, default):
        """
        Return the value of this setting. If the value is None, set it to `default` and return that instead.
        """
        if self.value is None:
            self.value = default

        return self.value

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = 'configurationsettings'
    __table_args__ = (
        UniqueConstraint('external_integration_id', 'library_id', 'key'),
    )

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)
    key = Column(Unicode, index=True)
    _value = Column(Unicode, name="value")

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    external_integration_id = Column(Integer, ForeignKey('externalintegrations.id'), index=True)
    library_id = Column(Integer, ForeignKey('libraries.id'), index=True)

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    @property
    def library(self):
        _db = Session.object_session(self)
        if self.library_id:
            return get_one(_db, Library, id=self.library_id)
        return None

    @hybrid_property
    def value(self):
        """
        What's the current value of this configuration setting?

        If not present, the value may be inherited from some other ConfigurationSetting.
        """
        if self._value:             # An explicitly set value always takes precedence.
            return self._value
        elif self.library_id and self.external_integration:
            # This is a library-specific specialization of an ExternalIntegration. Treat
            # the value set on the ExternalIntegration as a default.
            return self.for_externalintegration(self.key, self.external_integration).value
        elif self.library_id:
            # This is a library-specific setting. Treat the site-wide value as a default.
            _db = Session.object_session(self)
            return self.sitewide(_db, self.key).value

        return self._value

    @value.setter
    def value(self, new_value):
        self._value = new_value

    @property
    def is_secret(self):
        """Should the value of this key be treated as secret?"""
        return self._is_secret(self.key)

    @property
    def bool_value(self):
        """
        Turn the value into a boolean if possible.

        :return: A boolean, or None if there is no value.
        """
        if self.value is None:  # doing `if self.value` misses on explicit boolean False
            return None
        else:
            return True if str(self.value).lower() in self.MEANS_YES else False

    @property
    def int_value(self):
        """
        Turn the value into an int if possible.

        :return: An integer, or None if there is no value.
        """
        try:
            if isinstance(self.value, bool):    # int(True) and int(False) eval to 1/0, respectively
                raise TypeError

            return int(float(self.value))   # cast to float first, to turn '1.1' into 1.1, which can convert to 1
        except (ValueError, TypeError):
            ...

        return None

    @property
    def float_value(self):
        """
        Turn the value into an float if possible.

        :return: A float, or None if the value cannot be cast to float.
        """
        try:
            if isinstance(self.value, bool):    # int(True) and int(False) eval to 1/0, respectively
                raise TypeError

            return float(self.value)
        except (ValueError, TypeError):
            ...

        return None

    @property
    def json_value(self):
        """
        Interpret the value as JSON if possible.

        :return: An object, or None if there is no value.
        """
        try:
            return json.loads(self.value)
        except (TypeError, json.decoder.JSONDecodeError):
            ...

        return None

    ##### Class Methods ######################################################  # noqa: E266

    @classmethod
    def sitewide_secret(cls, _db, key):
        """
        Find or create a sitewide shared secret.

        The value of this setting doesn't matter, only that it's unique across the site and that
        it's always available.
        """
        secret = ConfigurationSetting.sitewide(_db, key)

        if not secret.value:
            secret.value = generate_secret()
            _db.commit()    # Commit to get this in the database ASAP.

        return secret.value

    @classmethod
    def explain(cls, _db, include_secrets=False):
        """Explain all site-wide ConfigurationSettings."""
        lines = []
        site_wide_settings = []

        settings_to_explain = _db.query(ConfigurationSetting).filter(
                ConfigurationSetting.library_id == None                 # noqa: E711
            ).filter(
                ConfigurationSetting.external_integration == None       # noqa: E711
            )

        for setting in settings_to_explain:
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
        """Find or create a ConfigurationSetting for the given ExternalIntegration."""
        _db = Session.object_session(externalintegration)
        return cls.for_library_and_externalintegration(_db, key, None, externalintegration)

    @classmethod
    def for_library_and_externalintegration(cls, _db, key, library, external_integration):
        """
        Find or create a ConfigurationSetting associated with a Library and an ExternalIntegration.
        """
        library_id = None

        if library:
            library_id = library.id

        (setting, _) = get_one_or_create(_db, ConfigurationSetting,
                                         library_id=library_id,
                                         external_integration=external_integration,
                                         key=key)

        return setting

    ##### Private Class Methods ##############################################  # noqa: E266

    @classmethod
    def _is_secret(self, key):
        """
        Should the value of the given key be treated as secret?

        This will have to do, in the absence of programmatic ways of saying that a specific
        setting should be treated as secret.
        """
        return any(keyword in key.lower() for keyword in self.SECRET_SETTING_KEYWORDS)


# Join tables for many-to-many relationships

libraries_audiences = Table(
    'libraries_audiences', Base.metadata,
    Column('library_id', Integer, ForeignKey('libraries.id'), index=True, nullable=False),
    Column('audience_id', Integer, ForeignKey('audiences.id'), index=True, nullable=False),
    UniqueConstraint('library_id', 'audience_id'),
)


class Admin(Base):
    ##### Class Constants ####################################################  # noqa: E266

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    def check_password(self, raw_password):
        return check_password_hash(self.password, raw_password)

    def __repr__(self):
        return f"<Admin: username={self.username}>"

    ##### SQLAlchemy Table properties ########################################  # noqa: E266

    __tablename__ = 'admins'

    ##### SQLAlchemy non-Column components ###################################  # noqa: E266

    ##### SQLAlchemy Columns #################################################  # noqa: E266

    id = Column(Integer, primary_key=True)
    username = Column(Unicode, index=True, unique=True, nullable=False)
    password = Column(Unicode, index=True)

    ##### SQLAlchemy Relationships ###########################################  # noqa: E266

    ##### SQLAlchemy Field Validation ########################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    @classmethod
    def make_password(cls, raw_password):
        return generate_password_hash(raw_password).decode('utf-8')

    @classmethod
    def authenticate(cls, _db, username, password):
        """
        Finds an authenticated Admin by username and password

        :return: Admin or None
        """
        admin = None
        if _db.query(Admin).count() == 0:   # No admins exist yet, create this one
            (admin, _) = create(_db, Admin, username=username)
            admin.password = cls.make_password(password)
        else:
            admin = get_one(_db, Admin, username=username)
            if admin and not admin.check_password(password):
                admin = None

        return admin

    ##### Private Class Methods ##############################################  # noqa: E266
