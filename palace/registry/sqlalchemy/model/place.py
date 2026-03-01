"""Place model for geographic locations."""

from __future__ import annotations

import json

import uszipcode
from geoalchemy2 import Geometry
from sqlalchemy import Column, ForeignKey, Integer, Unicode, UniqueConstraint, func
from sqlalchemy.orm import backref, relationship
from sqlalchemy.sql.expression import and_, or_, select

from palace.registry.config import Configuration
from palace.registry.sqlalchemy.constants import LibraryType
from palace.registry.sqlalchemy.model.base import Base
from palace.registry.sqlalchemy.util import get_one, get_one_or_create


class Place(Base):
    __tablename__ = "places"

    # These are the kinds of places we keep track of. These are not
    # supposed to be precise terms. Each census-designated place is
    # called a 'city', even if it's not a city in the legal sense.
    # Countries that call their top-level administrative divisions something
    # other than 'states' can still use 'state' as their type. (But see
    # LibraryType.ADMINISTRATIVE_DIVISION_TYPES.)
    NATION = "nation"
    STATE = "state"
    COUNTY = "county"
    CITY = "city"
    POSTAL_CODE = "postal_code"
    LIBRARY_SERVICE_AREA = "library_service_area"
    EVERYWHERE = "everywhere"

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
    parent_id = Column(Integer, ForeignKey("places.id"), index=True)

    children = relationship(
        "Place", backref=backref("parent", remote_side=[id]), lazy="joined"
    )

    # The geography of the place itself. It is stored internally as a
    # geometry, which means we have to cast to Geography when doing
    # calculations.
    geometry = Column(Geometry(srid=4326), nullable=True)

    aliases = relationship("PlaceAlias", backref="place")

    service_areas = relationship("ServiceArea", backref="place")

    @classmethod
    def everywhere(cls, _db):
        """Return a special Place that represents everywhere.

        This place has no .geometry, so attempts to use it in
        geographic comparisons will fail.
        """
        place, is_new = get_one_or_create(
            _db,
            Place,
            type=cls.EVERYWHERE,
            create_method_kwargs=dict(
                external_id="Everywhere", external_name="Everywhere"
            ),
        )
        return place

    @classmethod
    def default_nation(cls, _db):
        """Return the default nation for this library registry.

        If an incoming coverage area doesn't mention a nation, we'll
        assume it's within this nation.

        :return: The default nation, if one can be found. Otherwise, None.
        """
        from palace.registry.sqlalchemy.model.configuration_setting import (
            ConfigurationSetting,
        )

        default_nation = None
        abbreviation = ConfigurationSetting.sitewide(
            _db, Configuration.DEFAULT_NATION_ABBREVIATION
        ).value
        if abbreviation:
            default_nation = get_one(
                _db, Place, type=Place.NATION, abbreviated_name=abbreviation
            )
            if not default_nation:
                import logging

                logging.error("Could not look up default nation %s", abbreviation)
        return default_nation

    @classmethod
    def larger_place_types(cls, type):
        """Return a list of place types known to be bigger than `type`.

        Places don't form a strict heirarchy. In particular, ZIP codes
        are not 'smaller' than cities. But counties and cities are
        smaller than states, and states are smaller than nations, so
        if you're searching inside a state for a place called "Japan",
        you know that the nation of Japan is not what you're looking
        for.
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
        """Try to extract a place type from a name.

        :return: A 2-tuple (place_name, place_type)

        e.g. "Kern County" becomes ("Kern", Place.COUNTY)
        "Arizona State" becomes ("Arizona", Place.STATE)
        "Chicago" becaomes ("Chicago", None)
        """
        check = place_name.lower()
        place_type = None
        if check.endswith(" county"):
            place_name = place_name[:-7]
            place_type = Place.COUNTY

        if check.endswith(" state"):
            place_name = place_name[:-6]
            place_type = Place.STATE
        return place_name, place_type

    @classmethod
    def lookup_by_name(cls, _db, name, place_type=None):
        """Look up one or more Places by name."""
        if not place_type:
            name, place_type = cls.parse_name(name)
        qu = (
            _db.query(Place)
            .outerjoin(PlaceAlias)
            .filter(
                or_(
                    Place.external_name == name,
                    Place.abbreviated_name == name,
                    PlaceAlias.name == name,
                )
            )
        )
        if place_type:
            qu = qu.filter(Place.type == place_type)
        else:
            # The place type "county" is excluded unless it was
            # explicitly asked for (e.g. "Cook County"). This is to
            # avoid ambiguity in the many cases when a state contains
            # a county and a city with the same name. In all realistic
            # cases, someone using "Foo" to talk about a library
            # service area is referring to the city of Foo, not Foo
            # County -- if they want Foo County they can say "Foo
            # County".
            qu = qu.filter(Place.type != Place.COUNTY)
        return qu

    @classmethod
    def lookup_one_by_name(cls, _db, name, place_type=None):
        return cls.lookup_by_name(_db, name, place_type).one()

    @classmethod
    def to_geojson(cls, _db, *places):
        """Convert one or more Place objects to a dictionary that will become
        a GeoJSON document when converted to JSON.
        """
        geojson = select([func.ST_AsGeoJSON(Place.geometry)]).where(
            Place.id.in_([x.id for x in places])
        )
        results = [x[0] for x in _db.execute(geojson)]
        if len(results) == 1:
            # There's only one item, and it is a valid
            # GeoJSON document on its own.
            return json.loads(results[0])

        # We have either more or less than one valid item.
        # In either case, a GeometryCollection is appropriate.
        body = {
            "type": "GeometryCollection",
            "geometries": [json.loads(x) for x in results],
        }
        return body

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
        return [x.strip() for x in reversed(name.split(",")) if x.strip()]

    @property
    def library_type(self):
        """If a library serves this place, what type of library does that make
        it?

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
    def human_friendly_name(self):
        """Generate the sort of string a human would recognize as an
        unambiguous name for this place.

        This is in some sense the opposite of parse_name.

        :return: A string, or None if there is no human-friendly name for
           this place.
        """
        if self.type == self.EVERYWHERE:
            # 'everywhere' is not a distinct place with a well-known name.
            return None
        if self.parent and self.parent.type == self.STATE:
            parent = self.parent.abbreviated_name or self.parent.external_name
            if self.type == Place.COUNTY:
                # Renfrew County, ON
                return f"{self.external_name} County, {parent}"
            elif self.type == Place.CITY:
                # Montgomery, AL
                return f"{self.external_name}, {parent}"

        # All other cases:
        #  93203
        #  Texas
        #  France
        return self.external_name

    def overlaps_not_counting_border(self, qu):
        """Modifies a filter to find places that have points inside this
        Place, not counting the border.

        Connecticut has no points inside New York, but the two states
        share a border. This method creates a more real-world notion
        of 'inside' that does not count a shared border.
        """
        intersects = Place.geometry.intersects(self.geometry)
        touches = func.ST_Touches(Place.geometry, self.geometry)
        return qu.filter(intersects).filter(touches == False)

    def lookup_inside(self, name, using_overlap=False, using_external_source=True):
        """Look up a named Place that is geographically 'inside' this Place.

        :param name: The name of a place, such as "Boston" or
        "Calabasas, CA", or "Cook County".

        :param using_overlap: If this is true, then place A is
        'inside' place B if their shapes overlap, not counting
        borders. For example, Montgomery is 'inside' Montgomery
        County, Alabama, and the United States. However, Alabama is
        not 'inside' Georgia (even though they share a border).

        If `using_overlap` is false, then place A is 'inside' place B
        only if B is the .parent of A. In this case, Alabama is
        considered to be 'inside' the United States, but Montgomery is
        not -- the only place it's 'inside' is Alabama. Checking this way
        is much faster, so it's the default.

        :param using_external_source: If this is True, then if no named
        place can be found in the database, the uszipcodes library
        will be used in an attempt to find some equivalent postal codes.

        :return: A Place object, or None if no match could be found.

        :raise MultipleResultsFound: If more than one Place with the
        given name is 'inside' this Place.

        """
        from sqlalchemy.exc import MultipleResultsFound
        from sqlalchemy.orm import aliased

        parts = Place.name_parts(name)
        if len(parts) > 1:
            # We're trying to look up a scoped name such as "Boston,
            # MA". `name_parts` has turned "Boston, MA" into ["MA",
            # "Boston"].
            #
            # Now we need to look for "MA" inside ourselves, and then
            # look for "Boston" inside the object we get back.
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

        # If we get here, it means we're looking up "Boston" within
        # Massachussets, or "Kern County" within the United States.
        # In other words, we expect to find at most one place with
        # this name inside the `must_be_inside` object.
        #
        # If we find more than one, it's an error. The name should
        # have been scoped better. This will happen if you search for
        # "Springfield" or "Lake County" within the United States,
        # instead of specifying which state you're talking about.
        from sqlalchemy.orm.session import Session

        _db = Session.object_session(self)
        qu = Place.lookup_by_name(_db, name).filter(Place.type != self.type)

        # Don't look in a place type known to be 'bigger' than this
        # place.
        exclude_types = Place.larger_place_types(self.type)
        qu = qu.filter(~Place.type.in_(exclude_types))

        if self.type == self.EVERYWHERE:
            # The concept of 'inside' is not relevant because every
            # place is 'inside' EVERYWHERE. We are really trying to
            # find one and only one place with a certain name.
            pass
        else:
            if using_overlap and self.geometry is not None:
                qu = self.overlaps_not_counting_border(qu)
            else:
                parent = aliased(Place)
                grandparent = aliased(Place)
                qu = qu.join(parent, Place.parent_id == parent.id)
                qu = qu.outerjoin(grandparent, parent.parent_id == grandparent.id)

                # For postal codes, but no other types of places, we
                # allow the lookup to skip a level. This lets you look
                # up "93203" within a state *or* within the nation.
                postal_code_grandparent_match = and_(
                    Place.type == Place.POSTAL_CODE,
                    grandparent.id == self.id,
                )
                qu = qu.filter(or_(Place.parent == self, postal_code_grandparent_match))

        places = qu.all()
        if len(places) == 0:
            if using_external_source:
                # We don't have any matching places in the database _now_,
                # but there's a possibility we can find a representative
                # postal code.
                return self.lookup_one_through_external_source(name)
            else:
                # We're not allowed to use uszipcodes, probably
                # because this method was called by
                # lookup_through_external_source.
                return None
        if len(places) > 1:
            raise MultipleResultsFound(
                "More than one place called {} inside {}.".format(
                    name, self.external_name
                )
            )
        return places[0]

    def lookup_one_through_external_source(self, name):
        """Use an external source to find a Place that is a) inside `self`
        and b) identifies the place human beings call `name`.

        Currently the only way this might work is when using
        uszipcodes to look up a city inside a state. In this case the result
        will be a Place representing one of the city's postal codes.

        :return: A Place, or None if the lookup fails.
        """
        if self.type != Place.STATE:
            # uszipcodes keeps track of places in terms of their state.
            return None

        search = uszipcode.SearchEngine(
            db_file_path=f"{Configuration.DATADIR}/simple_db.sqlite"
        )
        state = self.abbreviated_name
        uszipcode_matches = []
        if (
            state in search.state_to_city_mapper
            and name in search.state_to_city_mapper[state]
        ):
            # The given name is an exact match for one of the
            # cities. Let's look up every ZIP code for that city.
            # `returns=None` here means to not limit the number of results.
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
        """Find all Libraries with a ServiceArea whose Place overlaps
        this Place, not counting the border.

        A Library whose ServiceArea borders this place, but does not
        intersect this place, is not counted. This way, the state
        library from the next state over doesn't count as serving your
        state.
        """
        from sqlalchemy.orm.session import Session

        from palace.registry.sqlalchemy.model.library import Library
        from palace.registry.sqlalchemy.model.service_area import ServiceArea

        _db = Session.object_session(self)
        qu = _db.query(Library).join(Library.service_areas).join(ServiceArea.place)
        qu = self.overlaps_not_counting_border(qu)
        return qu

    def __repr__(self):
        if self.parent:
            parent = self.parent.external_name
        else:
            parent = None
        if self.abbreviated_name:
            abbr = "abbr=%s " % self.abbreviated_name
        else:
            abbr = ""
        output = "<Place: {} type={} {}external_id={} parent={}>".format(
            self.external_name,
            self.type,
            abbr,
            self.external_id,
            parent,
        )
        return str(output)


class PlaceAlias(Base):
    """An alternate name for a place."""

    __tablename__ = "placealiases"

    id = Column(Integer, primary_key=True)
    place_id = Column(Integer, ForeignKey("places.id"), index=True)
    name = Column(Unicode, index=True)
    language = Column(Unicode(3), index=True)

    __table_args__ = (UniqueConstraint("place_id", "name", "language"),)
