import json

import pytest
from sqlalchemy import func
from sqlalchemy.orm.exc import MultipleResultsFound

from palace.registry.config import Configuration
from palace.registry.sqlalchemy.model.configuration_setting import ConfigurationSetting
from palace.registry.sqlalchemy.model.place import Place, PlaceAlias
from palace.registry.sqlalchemy.util import get_one_or_create
from tests.fixtures.database import DatabaseTransactionFixture


class TestPlace:
    def test_creation(self, db: DatabaseTransactionFixture):
        # Create some US states represented by points.
        # (Rather than by multi-polygons, as they will be represented in
        # the actual application.)
        new_york, is_new = get_one_or_create(
            db.session,
            Place,
            type=Place.STATE,
            external_id="04",
            external_name="New York",
            create_method_kwargs=dict(geometry="SRID=4326;POINT(-75 43)"),
        )
        assert is_new is True

        new_mexico, is_new = get_one_or_create(
            db.session,
            Place,
            type=Place.STATE,
            external_id="21",
            external_name="New Mexico",
            create_method_kwargs=dict(geometry="SRID=4326;POINT(-106 34)"),
        )

        connecticut, is_new = get_one_or_create(
            db.session,
            Place,
            type=Place.STATE,
            external_id="14",
            external_name="Connecticut",
            create_method_kwargs=dict(geometry="SRID=4326;POINT(-73.7 41.6)"),
        )

        # Create a city within one of the states, again represented by
        # a point rather than an outline.
        lake_placid, is_new = get_one_or_create(
            db.session,
            Place,
            type=Place.CITY,
            external_id="1234",
            external_name="Lake Placid",
            parent=new_york,
            create_method_kwargs=dict(geometry="SRID=4326;POINT(-73.59 44.17)"),
        )
        assert lake_placid.parent == new_york
        assert new_york.children == [lake_placid]
        assert new_mexico.children == []

        # Query the database to find states ordered by distance from
        # Lake Placid.
        distance = func.ST_DistanceSphere(lake_placid.geometry, Place.geometry)
        places = (
            db.session.query(Place)
            .filter(Place.type == Place.STATE)
            .order_by(distance)
            .add_columns(distance)
        )

        # We can find the distance in kilometers between the 'Lake
        # Placid' point and the points representing the other states.
        assert [(x[0].external_name, int(x[1] / 1000)) for x in places] == [
            ("New York", 172),
            ("Connecticut", 285),
            ("New Mexico", 2993),
        ]

    def test_aliases(self, db: DatabaseTransactionFixture):
        new_york, is_new = get_one_or_create(
            db.session,
            Place,
            type=Place.STATE,
            external_id="04",
            external_name="New York",
            create_method_kwargs=dict(geometry="SRID=4326;POINT(-75 43)"),
        )
        alias, is_new = get_one_or_create(
            db.session,
            PlaceAlias,
            place=new_york,
            name="New York State",
            language="eng",
        )
        assert new_york.aliases == [alias]

    def test_default_nation(self, db: DatabaseTransactionFixture):
        m = Place.default_nation

        # We start out with no default nation.
        assert m(db.session) is None

        # The abbreviation of the default nation is stored in the
        # DEFAULT_NATION_ABBREVIATION setting.
        setting = ConfigurationSetting.sitewide(
            db.session, Configuration.DEFAULT_NATION_ABBREVIATION
        )
        assert setting.value is None

        # Set the default nation to the United States.
        setting.value = db.crude_us.abbreviated_name
        assert m(db.session) == db.crude_us

        # If there's no nation with this abbreviation,
        # there is no default nation.
        setting.value = "LL"
        assert m(db.session) is None

    def test_to_geojson(self, db: DatabaseTransactionFixture):

        # If you ask for the GeoJSON of one place, that place is
        # returned as-is.
        zip1 = db.zip_10018
        geojson = Place.to_geojson(db.session, zip1)
        assert json.loads(db.zip_10018_geojson) == geojson

        # If you ask for GeoJSON of several places, it's returned as a
        # GeometryCollection document.
        zip2 = db.zip_11212
        geojson = Place.to_geojson(db.session, zip1, zip2)
        assert geojson["type"] == "GeometryCollection"

        # There are two geometries in this document -- one for each
        # Place we passed in.
        geometries = geojson["geometries"]
        assert len(geometries) == 2
        for check in [db.zip_10018_geojson, db.zip_11212_geojson]:
            assert json.loads(check) in geojson["geometries"]

    def test_overlaps_not_counting_border(self, db: DatabaseTransactionFixture):
        """Test that overlaps_not_counting_border does not count places
        that share a border as intersecting, the way the PostGIS
        'intersect' logic does.
        """
        nyc = db.new_york_city
        new_york = db.new_york_state
        connecticut = db.connecticut_state

        def s_i(place1, place2):
            """Use overlaps_not_counting_border to provide a boolean answer
            to the question: does place 2 strictly intersect place 1?
            """
            qu = db.session.query(Place)
            qu = place1.overlaps_not_counting_border(qu)
            return place2 in qu.all()

        # Places that contain each other intersect.
        assert s_i(nyc, new_york) is True
        assert s_i(new_york, nyc) is True

        # Places that don't share a border don't intersect.
        assert s_i(nyc, connecticut) is False
        assert s_i(connecticut, nyc) is False

        # Connecticut and New York share a border, so PostGIS says they
        # intersect, but they don't "intersect" in the everyday sense,
        # so overlaps_not_counting_border excludes them.
        assert s_i(new_york, connecticut) is False
        assert s_i(connecticut, new_york) is False

    def test_parse_name(self):
        m = Place.parse_name
        assert m("Kern County") == ("Kern", Place.COUNTY)
        assert m("New York State") == ("New York", Place.STATE)
        assert m("Chicago, IL") == ("Chicago, IL", None)

    def test_name_parts(self):
        m = Place.name_parts
        assert m("Boston, MA") == ["MA", "Boston"]
        assert m("Boston, MA,") == ["MA", "Boston"]
        assert m("Anytown, USA") == ["USA", "Anytown"]
        assert m("Lake County, Ohio, US") == ["US", "Ohio", "Lake County"]

    def test_human_friendly_name(self, db: DatabaseTransactionFixture):
        # Places of different types are given good-looking
        # human-friendly names.

        nation = db.place(external_name="United States", type=Place.NATION)
        assert "United States" == nation.human_friendly_name

        state = db.place(
            external_name="Alabama",
            abbreviated_name="AL",
            type=Place.STATE,
            parent=nation,
        )
        assert "Alabama" == state.human_friendly_name

        city = db.place(external_name="Montgomery", type=Place.CITY, parent=state)
        assert "Montgomery, AL" == city.human_friendly_name

        county = db.place(external_name="Montgomery", type=Place.COUNTY, parent=state)
        assert "Montgomery County, AL" == county.human_friendly_name

        postal_code = db.place(
            external_name="36043", type=Place.POSTAL_CODE, parent=state
        )
        assert "36043" == postal_code.human_friendly_name

        # This shouldn't happen, but just in case: the state's full
        # name is used if it has no abbreviated name.
        state.abbreviated_name = None
        assert "Montgomery, Alabama" == city.human_friendly_name
        assert "Montgomery County, Alabama" == county.human_friendly_name

        # 'everywhere' is not a distinct place with a well-known name.
        everywhere = db.place(type=Place.EVERYWHERE)
        assert None == everywhere.human_friendly_name

    def test_lookup_by_name(self, db: DatabaseTransactionFixture):

        # There are two places in California called 'Santa Barbara': a
        # city, and a county (which includes the city).
        sb_city = db.place(external_name="Santa Barbara", type=Place.CITY)
        sb_county = db.place(external_name="Santa Barbara", type=Place.COUNTY)

        # If we look up "Santa Barbara" by name, we get the city.
        m = Place.lookup_by_name
        assert m(db.session, "Santa Barbara").all() == [sb_city]

        # To get Santa Barbara County, we have to refer to
        # "Santa Barbara County"
        assert m(db.session, "Santa Barbara County").all() == [sb_county]

    def test_lookup_inside(self, db: DatabaseTransactionFixture):
        us = db.crude_us
        zip_10018 = db.zip_10018
        nyc = db.new_york_city
        new_york = db.new_york_state
        connecticut = db.connecticut_state
        manhattan_ks = db.manhattan_ks
        kings_county = db.crude_kings_county
        zip_12601 = db.zip_12601

        # In most cases, we want to test that both versions of
        # lookup_inside() return the same result.
        def lookup_both_ways(parent, name, expect):
            assert expect == parent.lookup_inside(name, using_overlap=True)
            assert expect == parent.lookup_inside(name, using_overlap=False)

        everywhere = Place.everywhere(db.session)
        lookup_both_ways(everywhere, "US", us)
        lookup_both_ways(everywhere, "NY", new_york)
        lookup_both_ways(us, "NY", new_york)

        lookup_both_ways(new_york, "10018", zip_10018)
        lookup_both_ways(us, "10018, NY", zip_10018)
        lookup_both_ways(us, "New York, NY", nyc)
        lookup_both_ways(new_york, "New York", nyc)

        # Test that the disambiguators "State" and "County" are handled
        # properly.
        lookup_both_ways(us, "New York State", new_york)
        lookup_both_ways(us, "Kings County, NY", kings_county)
        lookup_both_ways(us, "New York State", new_york)

        lookup_both_ways(us, "Manhattan, KS", manhattan_ks)
        lookup_both_ways(us, "Manhattan, Kansas", manhattan_ks)

        lookup_both_ways(new_york, "Manhattan, KS", None)
        lookup_both_ways(connecticut, "New York", None)
        lookup_both_ways(new_york, "Manhattan, KS", None)
        lookup_both_ways(connecticut, "New York", None)
        lookup_both_ways(connecticut, "New York, NY", None)
        lookup_both_ways(connecticut, "10018", None)

        # Even though the parent of a ZIP code is a state, special
        # code allows you to look them up within the nation.
        lookup_both_ways(us, "10018", zip_10018)
        lookup_both_ways(new_york, "10018", zip_10018)

        # You can't find a place 'inside' itself.
        lookup_both_ways(us, "US", None)
        lookup_both_ways(new_york, "NY, US, 10018", None)

        # Or 'inside' a place that's known to be smaller than it.
        lookup_both_ways(kings_county, "NY", None)
        lookup_both_ways(us, "NY, 10018", None)
        lookup_both_ways(zip_10018, "NY", None)

        # There is a limited ability to look up places even when the
        # name of the city is not in the database -- a representative
        # postal code is returned. This goes through
        # lookup_one_through_external_source, which is tested in more
        # detail below.
        lookup_both_ways(new_york, "Poughkeepsie", zip_12601)

        # Now test cases where using_overlap makes a difference.
        #
        # First, the cases where using_overlap=True performs better.
        #

        # Looking up the name of a county by itself only works with
        # using_overlap=True, because the .parent of a county is its
        # state, not the US.
        #
        # Many county names are ambiguous, but this lets us parse
        # the ones that are not.
        assert (
            everywhere.lookup_inside("Kings County, US", using_overlap=True)
            == kings_county
        )

        # Neither of these is obviously better.
        assert us.lookup_inside("Manhattan") is None
        with pytest.raises(MultipleResultsFound) as exc:
            us.lookup_inside("Manhattan", using_overlap=True)
        assert "More than one place called Manhattan inside United States." in str(
            exc.value
        )

        # Now the cases where using_overlap=False performs better.

        # "New York, US" is a little ambiguous, but they probably mean
        # the state.
        assert us.lookup_inside("New York") == new_york
        with pytest.raises(MultipleResultsFound) as exc:
            us.lookup_inside("New York", using_overlap=True)
        assert "More than one place called New York inside United States." in str(
            exc.value
        )

        # "New York, New York" can only be parsed by parentage.
        assert us.lookup_inside("New York, New York") == nyc
        with pytest.raises(MultipleResultsFound) as exc:
            us.lookup_inside("New York, New York", using_overlap=True)
        assert "More than one place called New York inside United States." in str(
            exc.value
        )

        # Using geographic overlap has another problem -- although the
        # name of the method is 'lookup_inside', we're actually
        # checking for _intersection_. Places that overlap are treated
        # as being inside *each other*.
        assert zip_10018.lookup_inside("New York", using_overlap=True) == nyc
        assert zip_10018.lookup_inside("New York", using_overlap=False) is None

    def test_lookup_one_through_external_source(self, db: DatabaseTransactionFixture):
        # We're going to find the approximate location of Poughkeepsie
        # even though the database doesn't have a Place named
        # "Poughkeepsie".
        #
        # We're able to do this because uszipcode knows which ZIP
        # codes are in Poughkeepsie, and we do have a Place for one of
        # those ZIP codes.
        zip_12601 = db.zip_12601
        new_york = db.new_york_state
        connecticut = db.connecticut_state

        m = new_york.lookup_one_through_external_source
        poughkeepsie_zips = m("Poughkeepsie")

        # There are three ZIP codes in Poughkeepsie, and uszipcode
        # knows about all of them, but the only Place returned by
        # lookup_through_external_source is the one for the ZIP code
        # we know about.
        assert poughkeepsie_zips == zip_12601

        # If we ask about a real place but there is no corresponding
        # postal code Place in the database, we get nothing.
        assert m("Woodstock") is None

        # Similarly if we ask about a nonexistent place.
        assert m("ZXCVB") is None

        # Or if we try to use uszipcode on a place that's not in the US.
        ontario = db.place("35", "Ontario", Place.STATE, "ON", None, None)
        assert ontario.lookup_one_through_external_source("Hamilton") is None

        # Calling this method on a Place that's not a state doesn't
        # make sense (because uszipcode only knows about cities within
        # states), and the result is always None.
        assert zip_12601.lookup_one_through_external_source("Poughkeepsie") is None

        # lookup_one_through_external_source operates on the same
        # rules as lookup_inside -- the city you're looking up must be
        # geographically inside the Place whose method you're calling.
        assert connecticut.lookup_one_through_external_source("Poughkeepsie") is None

    def test_served_by(self, db: DatabaseTransactionFixture):
        zip = db.zip_10018
        nyc = db.new_york_city
        new_york = db.new_york_state
        connecticut = db.connecticut_state

        # There are two libraries here...
        nypl = db.library("New York Public Library", eligibility_areas=[nyc])
        ct_state = db.library(
            "Connecticut State Library", eligibility_areas=[connecticut]
        )

        # ...but only one serves the 10018 ZIP code.
        assert zip.served_by().all() == [nypl]

        assert nyc.served_by().all() == [nypl]
        assert connecticut.served_by().all() == [ct_state]

        # New York and Connecticut share a border, and the Connecticut
        # state library serves the entire state, including the
        # border. Internally, we use overlaps_not_counting_border() to avoid
        # concluding that the Connecticut state library serves New
        # York.
        assert new_york.served_by().all() == [nypl]
