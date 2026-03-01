"""Library and LibraryAlias models."""

from __future__ import annotations

import random
import re
import string
import uuid
from collections import Counter, defaultdict

from geoalchemy2 import Geography, Geometry
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Unicode,
    UniqueConstraint,
    and_,
    case,
    cast,
    collate,
    func,
    literal_column,
    or_,
    outerjoin,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import (
    aliased,
    relationship,
    validates,
)
from sqlalchemy.orm.session import Session
from sqlalchemy.sql.expression import join, select

from palace.registry.sqlalchemy.model.audience import libraries_audiences
from palace.registry.sqlalchemy.model.base import Base
from palace.registry.sqlalchemy.util import get_one, get_one_or_create
from palace.registry.util import GeometryUtility
from palace.registry.util.datetime_helpers import utc_now
from palace.registry.util.language import LanguageCodes


class Library(Base):
    """An entry in this table corresponds more or less to an OPDS server.

    Libraries generally serve everyone in a specific list of
    Places. Libraries may also focus on a subset of the places they
    serve, and may restrict their service to certain audiences.
    """

    __tablename__ = "libraries"

    id = Column(Integer, primary_key=True)

    # The official name of the library.  This is not unique because
    # there are many "Springfield Public Library"s.  This is nullable
    # because there's a period during initial registration where a
    # library has no name. (TODO: we might be able to change this.)
    name = Column(Unicode, index=True)

    # Human-readable explanation of who the library serves.
    description = Column(Unicode)

    # An internally generated unique URN. This is used in controller
    # URLs to identify a library. A registry will always use the same
    # URN to identify a given library, even if the library's OPDS
    # server changes.
    internal_urn = Column(
        Unicode,
        nullable=False,
        index=True,
        unique=True,
        default=lambda: "urn:uuid:" + str(uuid.uuid4()),
    )

    # The URL to the library's Authentication for OPDS document. This
    # URL may change over time as libraries move to different servers.
    # This URL is generally unique, but that's not a database
    # requirement, since a single library could potentially have two
    # registry entries.
    authentication_url = Column(Unicode, index=True)

    # The URL to the library's OPDS server root.
    opds_url = Column(Unicode)

    # The URL to the library's patron-facing web page.
    web_url = Column(Unicode)

    # When our record of this library was last updated.
    timestamp = Column(
        DateTime(timezone=True),
        index=True,
        default=utc_now,
        onupdate=utc_now,
    )

    # The library's logo, as a web url
    logo_url = Column(Unicode)

    # Constants for determining which stage a library is in.
    #
    # Which stage the library is actually in depends on the
    # combination of Library.library_stage (the library's opinion) and
    # Library.registry_stage (the registry's opinion).
    #
    # If either value is CANCELLED_STAGE, the library is in
    # CANCELLED_STAGE.
    #
    # Otherwise, if either value is TESTING_STAGE, the library is in
    # TESTING_STAGE.
    #
    # Otherwise, the library is in PRODUCTION_STAGE.
    TESTING_STAGE = "testing"  # Library should show up in test feed
    PRODUCTION_STAGE = "production"  # Library should show up in production feed
    CANCELLED_STAGE = "cancelled"  # Library should not show up in any feed
    stage_enum = Enum(
        TESTING_STAGE, PRODUCTION_STAGE, CANCELLED_STAGE, name="library_stage"
    )

    # The library's opinion about which stage a library should be in.
    _library_stage = Column(
        stage_enum,
        index=True,
        nullable=False,
        default=TESTING_STAGE,
        name="library_stage",
    )

    # The registry's opinion about which stage a library should be in.
    registry_stage = Column(
        stage_enum, index=True, nullable=False, default=TESTING_STAGE
    )

    # Can people get books from this library without authenticating?
    #
    # We store this specially because it might be useful to filter
    # for libraries of this type.
    anonymous_access = Column(Boolean, default=False)

    # Can eligible people get credentials for this library through
    # an online registration process?
    #
    # We store this specially because it might be useful to filter
    # for libraries of this type.
    online_registration = Column(Boolean, default=False)

    # To issue Short Client Tokens for this library, the registry must
    # share a short name and a secret with them.
    short_name = Column(Unicode, index=True, unique=True)

    # The shared secret is also used to authenticate requests in the
    # case where a library's URL has changed.
    shared_secret = Column(Unicode)

    # A library may have alternate names, e.g. "BPL" for the Brooklyn
    # Public Library.
    aliases = relationship("LibraryAlias", backref="library")

    # A library may serve one or more geographic areas.
    service_areas = relationship("ServiceArea", backref="library")

    # A library may serve one or more specific audiences.
    audiences = relationship(
        "Audience", secondary="libraries_audiences", back_populates="libraries"
    )

    # The registry may have information about the library's
    # collections of materials. The registry doesn't need to know
    # details, but it's useful to know approximate counts when finding
    # libraries that serve specific language communities.
    collections = relationship("CollectionSummary", backref="library")

    # The registry may keep delegated patron identifiers (basically,
    # Adobe IDs) for a library's patrons. This allows the library's
    # patrons to decrypt Adobe ACS-encrypted books without having to
    # license separate Adobe Vendor ID and without the registry
    # knowing anything about the patrons.
    delegated_patron_identifiers = relationship(
        "DelegatedPatronIdentifier", backref="library"
    )

    # A library may have miscellaneous URIs associated with it. Generally
    # speaking, the registry is only concerned about these URIs insofar as
    # it needs to verify that they work.
    hyperlinks = relationship("Hyperlink", backref="library")

    settings = relationship(
        "ConfigurationSetting",
        backref="library",
        lazy="joined",
        cascade="all, delete",
    )

    # The PLS (Public Library Surveys) ID comes from the IMLS' annual survey
    # (it isn't generated by our database).  It enables us to gather data for metrics
    # such as number of covered branches and size of service population.
    PLS_ID = "pls_id"

    @validates("short_name")
    def validate_short_name(self, key, value):
        if not value:
            return value
        if "|" in value:
            raise ValueError("Short name cannot contain the pipe character.")
        return value.upper()

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
        """Generate a random short name for a library.

        Library short names are six uppercase letters.

        :param duplicate_check: Call this function to check whether a
            generated name is a duplicate.
        :param max_attempts: Stop trying to generate a name after this
            many failures.
        """
        attempts = 0
        choice = None
        while choice is None and attempts < max_attempts:
            choice = "".join([random.choice(string.ascii_uppercase) for i in range(6)])
            if duplicate_check and duplicate_check(choice):
                choice = None
            attempts += 1
        if choice is None:
            # This is very bad, but it's better to raise an exception
            # than to be stuck in an infinite loop.
            raise ValueError(
                "Could not generate random short name after %d attempts!" % attempts
            )
        return choice

    @hybrid_property
    def library_stage(self):
        return self._library_stage

    @library_stage.setter
    def library_stage(self, value):
        """A library can't unilaterally go from being in production to
        not being in production.
        """
        if self.in_production and value != self.PRODUCTION_STAGE:
            raise ValueError(
                "This library is already in production; only the registry can take it out of production."
            )
        self._library_stage = value

    @property
    def pls_id(self):
        from palace.registry.sqlalchemy.model.configuration_setting import (
            ConfigurationSetting,
        )

        return ConfigurationSetting.for_library(Library.PLS_ID, self)

    @property
    def number_of_patrons(self):
        from palace.registry.sqlalchemy.model.delegated_patron_identifier import (
            DelegatedPatronIdentifier,
        )

        db = Session.object_session(self)
        # This is only meaningful if the library is in production.
        if not self.in_production:
            return 0
        query = db.query(DelegatedPatronIdentifier).filter(
            DelegatedPatronIdentifier.type
            == DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID,
            DelegatedPatronIdentifier.library_id == self.id,
        )
        return query.count()

    @classmethod
    def patron_counts_by_library(self, _db, libraries):
        """Determine the number of registered Adobe Account IDs
        (~patrons) for each of the given libraries.

        :param _db: A database connection.
        :param libraries: A list of Library objects.
        :return: A dictionary mapping library IDs to patron counts.
        """
        from palace.registry.sqlalchemy.model.delegated_patron_identifier import (
            DelegatedPatronIdentifier,
        )

        # The concept of 'patron count' only makes sense for
        # production libraries.
        library_ids = [library.id for library in libraries if library.in_production]

        # Run the SQL query.
        counts = (
            select(
                [
                    DelegatedPatronIdentifier.library_id,
                    func.count(DelegatedPatronIdentifier.id),
                ],
            )
            .where(
                and_(
                    DelegatedPatronIdentifier.type
                    == DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID,
                    DelegatedPatronIdentifier.library_id.in_(library_ids),
                )
            )
            .group_by(DelegatedPatronIdentifier.library_id)
            .select_from(DelegatedPatronIdentifier)
        )
        rows = _db.execute(counts)

        # Convert the results to a dictionary.
        results = dict()
        for library_id, count in rows:
            results[library_id] = count

        return results

    @property
    def in_production(self):
        """Is this library in production?

        If both the library and the registry think it should be, it is.
        """
        prod = self.PRODUCTION_STAGE
        return self.library_stage == prod and self.registry_stage == prod

    @property
    def types(self):
        """Return any special types for this library.

        :yield: A sequence of code constants from LibraryTypes.
        """
        service_area = self.service_area
        if not service_area:
            return
        code = service_area.library_type
        if code:
            yield code

        # TODO: in the future, more types, e.g. audience-based, can go
        # here.

    @property
    def service_area(self):
        """Return the service area of this Library, assuming there is only
        one.

        :return: A Place, if there is one well-defined place this
        library serves; otherwise None.
        """
        from palace.registry.sqlalchemy.model.service_area import ServiceArea

        everywhere = None

        # Group the ServiceAreas by type.
        by_type = defaultdict(set)
        for a in self.service_areas:
            if not a.place:
                continue
            from palace.registry.sqlalchemy.model.place import Place

            if a.place.type == Place.EVERYWHERE:
                # We will only return 'everywhere' if we don't find
                # something more specific.
                everywhere = a.place
                continue
            by_type[a.type].add(a)

        # If there is a single focus area, use it.
        # Otherwise, if there is a single eligibility area, use that.
        service_area = None
        for area_type in ServiceArea.FOCUS, ServiceArea.ELIGIBILITY:
            if len(by_type[area_type]) == 1:
                [service_area] = by_type[area_type]
                if service_area.place:
                    return service_area.place

        # This library serves everywhere, and it doesn't _also_ serve
        # some more specific place.
        if everywhere:
            return everywhere

        # This library does not have one ServiceArea that stands out.
        return None

    @property
    def service_area_name(self):
        """Describe the library's service area in a short string a human would
        understand, e.g. "Kern County, CA".

        This library does the best it can to express a library's service
        area as the name of a single place, but it's not always possible
        since libraries can have multiple service areas.

        TODO: We'll want to fetch a library's ServiceAreas (and their
        Places) as part of the query that fetches libraries, so that
        this doesn't result in extra DB queries per library.

        :return: A string, or None if the library's service area can't be
           described as a short string.
        """
        if self.service_area:
            return self.service_area.human_friendly_name
        return None

    @classmethod
    def name_sort_key(cls):
        """Case-insensitive sort key for ORDER BY clauses.

        Here we use a collation for which spaces sort before letters,
        The "default" collation selects the locale specified at database
        creation time, which might or might not be appropriate.
        """
        return collate(func.upper(cls.name), "unicode")

    @classmethod
    def _feed_restriction(cls, production, library_field=None, registry_field=None):
        """Create a SQLAlchemy restriction that only finds libraries that
        ought to be in the given feed.

        :param production: A boolean. If True, then only libraries in
        the production stage should be included. If False, then
        libraries in the production or testing stages should be
        included.

        :return: A SQLAlchemy expression.
        """
        # The library's opinion
        if library_field is None:
            library_field = Library.library_stage
        # The registry's opinion
        if registry_field is None:
            registry_field = Library.registry_stage

        prod = cls.PRODUCTION_STAGE
        test = cls.TESTING_STAGE

        if production:
            # Both parties must agree that this library is
            # production-ready.
            return and_(library_field == prod, registry_field == prod)
        else:
            # Both parties must agree that this library is _either_
            # in the production stage or the testing stage.
            return and_(
                library_field.in_((prod, test)), registry_field.in_((prod, test))
            )

    @classmethod
    def relevant(cls, _db, target, language, audiences=None, production=True):
        """Find libraries that are most relevant for a user.

        :param target: The user's current location. May be a Geometry object or
        a 2-tuple (latitude, longitude).
        :param language: The ISO 639-1 code for the user's language.
        :param audiences: List of audiences the user is a member of.
        By default, only libraries with the PUBLIC audience are shown.
        :param production: If True, only libraries that are ready for
            production are shown.

        :return A Counter mapping Library objects to scores.
        """
        from palace.registry.sqlalchemy.model.audience import Audience
        from palace.registry.sqlalchemy.model.collection_summary import (
            CollectionSummary,
        )
        from palace.registry.sqlalchemy.model.place import Place
        from palace.registry.sqlalchemy.model.service_area import ServiceArea

        # Constants that determine the weights of different components of the score.
        # These may need to be adjusted when there are more libraries in the system to
        # test with.
        base_score = 1
        audience_factor = 1.01
        collection_size_factor = 1000
        focus_area_distance_factor = 0.005
        eligibility_area_distance_factor = 0.1
        focus_area_size_factor = 0.00000001
        score_threshold = 0.00001

        # By default, only show libraries that are for the general public.
        audiences = audiences or [Audience.PUBLIC]

        # Convert the target to a single point.
        if isinstance(target, tuple):
            target = GeometryUtility.point(*target)

        # Convert the language to 3-letter code.
        language_code = LanguageCodes.string_to_alpha_3(language)

        # Set up an alias for libraries and collection summaries for use in subqueries.
        libraries_collections = outerjoin(
            Library, CollectionSummary, Library.id == CollectionSummary.library_id
        ).alias("libraries_collections")

        # Check if each library has a public audience.
        public_audiences_subquery = (
            select([func.count()])
            .where(
                and_(
                    Audience.name == Audience.PUBLIC,
                    libraries_audiences.c.library_id
                    == libraries_collections.c.libraries_id,
                )
            )
            .select_from(libraries_audiences.join(Audience))
            .scalar_subquery()
        )

        # Check if each library has a non-public audience from
        # the user's audiences.
        non_public_audiences_subquery = (
            select([func.count()])
            .where(
                and_(
                    Audience.name != Audience.PUBLIC,
                    Audience.name.in_(audiences),
                    libraries_audiences.c.library_id
                    == libraries_collections.c.libraries_id,
                )
            )
            .select_from(libraries_audiences.join(Audience))
            .scalar_subquery()
        )

        # Increase the score if there was an audience match other than
        # public, and set it to 0 if there's no match at all.
        score = case(
            [
                # Audience match other than public.
                (
                    non_public_audiences_subquery != literal_column(str(0)),
                    literal_column(str(base_score * audience_factor)),
                ),
                # Public audience.
                (
                    public_audiences_subquery != literal_column(str(0)),
                    literal_column(str(base_score)),
                ),
            ],
            # No match.
            else_=literal_column(str(0)),
        )

        # Function that decreases exponentially as its input increases.
        def exponential_decrease(value):
            original_exponent = -1 * value
            # Prevent underflow and overflow errors by ensuring
            # the exponent is between -500 and 500.
            exponent = case(
                [
                    (original_exponent > 500, literal_column(str(500))),
                    (original_exponent < -500, literal_column(str(-500))),
                ],
                else_=original_exponent,
            )
            return func.exp(exponent)

        # Get the maximum collection size for the user's language.
        collections_by_size = (
            _db.query(CollectionSummary)
            .filter(CollectionSummary.language == language_code)
            .order_by(CollectionSummary.size.desc())
        )

        if collections_by_size.count() == 0:
            max = 0
        else:
            max = collections_by_size.first().size

        # Only take collection size into account in the ranking if there's at
        # least one library with a non-empty collection in the user's language.
        if max > 0:
            # If we don't have any information about a library's collection size,
            # we'll just say there's one book. That way the library is ranked above
            # a library we know has 0 books, but below any libraries with more.
            # Maybe this should be larger, or should consider languages other than
            # the user's language.
            estimated_size = case(
                [
                    (
                        libraries_collections.c.collectionsummaries_id == None,
                        literal_column("1"),
                    )
                ],
                else_=libraries_collections.c.collectionsummaries_size,
            )
            score_multiplier = 1 - exponential_decrease(
                1.0 * collection_size_factor * estimated_size / max
            )
            score = score * score_multiplier

        # Create a subquery for a type of service area.
        def service_area_subquery(type):
            return (
                select([Place.geometry, Place.type])
                .where(
                    and_(
                        ServiceArea.library_id == libraries_collections.c.libraries_id,
                        ServiceArea.type == type,
                    )
                )
                .select_from(join(ServiceArea, Place, ServiceArea.place_id == Place.id))
                .lateral()
            )

        # Find each library's eligibility areas.
        eligibility_areas_subquery = service_area_subquery(ServiceArea.ELIGIBILITY)

        # Find each library's focus areas.
        focus_areas_subquery = service_area_subquery(ServiceArea.FOCUS)

        # Get the minimum distance from the target to any service area returned
        # by the subquery, in km. If a service area is "everywhere", the distance
        # is 0.
        def min_distance(subquery):
            return (
                func.min(
                    case(
                        [(subquery.c.type == Place.EVERYWHERE, literal_column(str(0)))],
                        else_=func.ST_DistanceSphere(target, subquery.c.geometry),
                    )
                )
                / 1000
            )

        # Minimum distance to any eligibility area.
        eligibility_min_distance = min_distance(eligibility_areas_subquery)

        # Minimum distance to any focus area.
        focus_min_distance = min_distance(focus_areas_subquery)

        # Decrease the score based on how far away the library's eligibility area is.
        score = score * exponential_decrease(
            1.0 * eligibility_area_distance_factor * eligibility_min_distance
        )

        # Decrease the score based on how far away the library's focus area is.
        score = score * exponential_decrease(
            1.0 * focus_area_distance_factor * focus_min_distance
        )

        # Decrease the score based on the sum of the sizes of the library's focus areas, in km^2.
        # This currently  assumes that the library's focus areas don't overlap, which may not be true.
        # If a focus area is "everywhere", the size is the area of Earth (510 million km^2).
        focus_area_size = (
            func.sum(
                case(
                    [
                        (
                            focus_areas_subquery.c.type == Place.EVERYWHERE,
                            literal_column(str(510000000000000)),
                        )
                    ],
                    else_=func.ST_Area(focus_areas_subquery.c.geometry),
                )
            )
            / 1000000
        )
        score = score * exponential_decrease(
            1.0 * focus_area_size_factor * focus_area_size
        )

        # Rank the libraries by score, and remove any libraries
        # that are below the score threshold.
        library_id_and_score = (
            select(
                [
                    libraries_collections.c.libraries_id,
                    score.label("score"),
                ]
            )
            .having(score > literal_column(str(score_threshold)))
            .where(
                and_(
                    # Query for either the production feed or the testing feed.
                    cls._feed_restriction(
                        production,
                        libraries_collections.c.libraries_library_stage,
                        libraries_collections.c.libraries_registry_stage,
                    ),
                    # Limit to the collection summaries for the user's
                    # language. If a library has no collection for the
                    # language, it's still included.
                    or_(
                        libraries_collections.c.collectionsummaries_language
                        == language_code,
                        libraries_collections.c.collectionsummaries_language == None,
                    ),
                )
            )
            .select_from(libraries_collections)
            .group_by(
                libraries_collections.c.libraries_id,
                libraries_collections.c.collectionsummaries_id,
                libraries_collections.c.collectionsummaries_size,
            )
            .order_by(score.desc())
        )

        result = _db.execute(library_id_and_score)
        library_ids_and_scores = {r[0]: r[1] for r in result}
        # Look up the Library objects and return them with the scores.
        libraries = _db.query(Library).filter(
            Library.id.in_(list(library_ids_and_scores.keys()))
        )
        c = Counter()
        for library in libraries:
            c[library] = library_ids_and_scores[library.id]
        return c

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
        from palace.registry.sqlalchemy.model.place import Place
        from palace.registry.sqlalchemy.model.service_area import ServiceArea

        # We start with a single point on the globe. Call this Point
        # A.
        if isinstance(target, tuple):
            target = GeometryUtility.point(*target)
        target_geography = cast(target, Geography)

        # Find another point on the globe that's 150 kilometers
        # northeast of Point A. Call this Point B.
        other_point = func.ST_Project(
            target_geography, max_radius * 1000, func.radians(90.0)
        )
        other_point = cast(other_point, Geometry)

        # Determine the distance between Point A and Point B, in
        # radians. (150 kilometers is a different number of radians in
        # different parts of the world.)
        distance_to_other_point = func.ST_Distance(target, other_point)

        # Find all Places that are no further away from A than that
        # number of radians.
        nearby = func.ST_DWithin(target, Place.geometry, distance_to_other_point)

        # For each library served by such a place, calculate the
        # minimum distance between the library's service area and
        # Point A in meters.
        min_distance = func.min(func.ST_DistanceSphere(target, Place.geometry))

        qu = _db.query(Library).join(Library.service_areas).join(ServiceArea.place)
        qu = qu.filter(cls._feed_restriction(production))
        qu = qu.filter(nearby)
        qu = (
            qu.add_columns(min_distance)
            .group_by(Library.id)
            .order_by(min_distance.asc())
        )
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
            libraries_for_name = (
                cls.search_by_library_name(_db, library_query, here, production)
                .limit(max_libraries)
                .all()
            )
        else:
            libraries_for_name = []

        # We tack on any additional libraries that match a place query.
        if place_query:
            libraries_for_location = (
                cls.search_by_location_name(
                    _db, place_query, place_type, here, production
                )
                .limit(max_libraries)
                .all()
            )
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
        libraries_for_description = (
            cls.search_within_description(_db, query, here, production)
            .limit(max_libraries)
            .all()
        )

        return libraries_for_name + libraries_for_location + libraries_for_description

    @classmethod
    def search_by_library_name(cls, _db, name, here=None, production=True):
        """Find libraries whose name or alias matches the given name.

        :param name: Name of the library to search for.
        :param here: Order results by proximity to this location.
        :param production: If True, only libraries that are ready for
            production are shown.
        """
        name_matches = cls.fuzzy_match(Library.name, name)
        alias_matches = cls.fuzzy_match(LibraryAlias.name, name)
        partial_matches = cls.partial_match(Library.name, name)
        return cls.create_query(
            _db, here, production, name_matches, alias_matches, partial_matches
        )

    @classmethod
    def search_by_location_name(cls, _db, query, type=None, here=None, production=True):
        """Find libraries whose service area overlaps a place with
        the given name.

        :param query: Name of the place to search for.
        :param type: Restrict results to places of this type.
        :param here: Order results by proximity to this location.
        :param production: If True, only libraries that are ready for
            production are shown.
        """
        from palace.registry.sqlalchemy.model.place import Place, PlaceAlias
        from palace.registry.sqlalchemy.model.service_area import ServiceArea

        # For a library to match, the Place named by the query must
        # intersect a Place served by that library.
        named_place = aliased(Place)
        qu = (
            _db.query(Library)
            .join(Library.service_areas)
            .join(ServiceArea.place)
            .join(named_place, func.ST_Intersects(Place.geometry, named_place.geometry))
            .outerjoin(named_place.aliases)
        )
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
        from palace.registry.sqlalchemy.model.place import Place
        from palace.registry.sqlalchemy.model.service_area import ServiceArea

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
        """Find libraries whose descriptions include the search term.

        :param query: The string to search for.
        :param here: Order results by proximity to this location.
        :param production: If True, only libraries that are ready for
            production are shown.
        """
        description_matches = cls.fuzzy_match(Library.description, query)
        partial_matches = cls.partial_match(Library.description, query)
        return cls.create_query(
            _db, here, production, description_matches, partial_matches
        )

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
        from palace.registry.sqlalchemy.model.place import Place

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
        for indicator in "public library", "library":
            if indicator in place_query:
                place_query = place_query.replace(indicator, "").strip()

        place_query, place_type = Place.parse_name(place_query)

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
        long_value_is_approximate_match = is_long & close_enough
        exact_match = field.ilike(value)
        return or_(long_value_is_approximate_match, exact_match)

    @classmethod
    def partial_match(cls, field, value):
        """Create a SQL clause that attempts to match a partial value--e.g.
        just one word of a library's name--against the given field."""
        return field.ilike(f"%{value}%")

    def set_hyperlink(self, rel, *hrefs):
        """Make sure this library has a Hyperlink with the given `rel` that
        points to a Resource with one of the given `href`s.

        If there's already a matching Hyperlink, it will be returned
        unmodified. Otherwise, the first item in `hrefs` will be used
        as the basis for a new Hyperlink, or an existing Hyperlink
        will be modified to use the first item in `hrefs` as its
        Resource.

        :return: A 2-tuple (Hyperlink, is_modified). `is_modified`
            is True if a new Hyperlink was created _or_ an existing
            Hyperlink was modified.

        """
        from palace.registry.sqlalchemy.model.hyperlink import Hyperlink

        if not rel:
            raise ValueError("No link relation was specified")
        if not hrefs:
            raise ValueError("No Hyperlink hrefs were specified")
        default_href = hrefs[0]
        _db = Session.object_session(self)
        hyperlink, is_modified = get_one_or_create(
            _db,
            Hyperlink,
            library=self,
            rel=rel,
        )

        if hyperlink.href not in hrefs:
            hyperlink.href = default_href
            is_modified = True

        return hyperlink, is_modified

    @classmethod
    def get_hyperlink(cls, library, rel):
        pass

        link = [x for x in library.hyperlinks if x.rel == rel]
        if len(link) > 0:
            return link[0]


class LibraryAlias(Base):
    """An alternate name for a library."""

    __tablename__ = "libraryalias"

    id = Column(Integer, primary_key=True)
    library_id = Column(Integer, ForeignKey("libraries.id"), index=True)
    name = Column(Unicode, index=True)
    language = Column(Unicode(3), index=True)

    __table_args__ = (UniqueConstraint("library_id", "name", "language"),)
