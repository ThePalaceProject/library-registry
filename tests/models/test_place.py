"""
Tests for the Place, PlaceAlias, and ServiceArea models.
"""
import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func
from sqlalchemy.orm.exc import MultipleResultsFound

from library_registry.config import Configuration
from library_registry.model import ConfigurationSetting, Place
from library_registry.util.geo import Location


class TestPlaceModel:
    """
    Tests the Place and PlaceAlias models.

    Note that these tests rely heavily on the 'places' fixture from tests/conftest.py.
    """
    def test_relation_parent(self, places):
        """
        GIVEN: A Place object defined with another Place as its 'parent'
        WHEN:  The 'parent' attribute is examined
        THEN:  That attribute should contain a reference to the parent
        """
        assert places["new_york_city"].parent == places["new_york_state"]
        assert places["zip_10018"].parent == places["new_york_state"]
        assert places["boston_ma"].parent == places["massachusetts_state"]
        assert places["manhattan_ks"].parent == places["kansas_state"]

    def test_relation_children(self, places):
        """
        GIVEN: A Place object defined with another Place as its 'parent'
        WHEN:  The 'children' attribute of the parent object is examined
        THEN:  That attribute should contain a reference to the child object
        """
        assert places["zip_10018"] in places["new_york_state"].children
        assert places["new_york_city"] in places["new_york_state"].children
        assert places["boston_ma"] in places["massachusetts_state"].children
        assert places["manhattan_ks"] in places["kansas_state"].children

    def test_relation_alias(self, places):
        """
        GIVEN: A Place object which was referenced in the creation of a PlaceAlias object
        WHEN:  The 'aliases' attribute of that Place object is examined
        THEN:  That attribute should contain a reference to the PlaceAlias object
        """
        nyc_aliases = places["new_york_city"].aliases
        assert "Manhattan" in [x.name for x in nyc_aliases]
        assert "Brooklyn" in [x.name for x in nyc_aliases]
        assert "New York" in [x.name for x in nyc_aliases]

    def test_default_nation_unset(self, db_session, app):
        """
        GIVEN: The sitewide setting Configuration.DEFAULT_NATION_ABBREVIATION is unset
        WHEN:  Place.default_nation() is called on the test db
        THEN:  The Place.default_nation() method should return None
        """
        setting = ConfigurationSetting.sitewide(db_session, Configuration.DEFAULT_NATION_ABBREVIATION)
        assert setting.value is None
        assert Place.default_nation(db_session) is None
        db_session.delete(setting)
        db_session.commit()

    def test_default_nation_set(self, db_session, places, app):
        """
        GIVEN: The sitewide setting Configuration.DEFAULT_NATION_ABBREVIATION is explicitly set
        WHEN:  Place.default_nation() is called on the test db
        THEN:  The Place.default_nation() method should return the set value
        """
        setting = ConfigurationSetting.sitewide(db_session, Configuration.DEFAULT_NATION_ABBREVIATION)
        setting.value = places["crude_us"].abbreviated_name
        default_nation_place = Place.default_nation(db_session)
        assert isinstance(default_nation_place, Place)
        assert default_nation_place.abbreviated_name == places["crude_us"].abbreviated_name
        db_session.delete(setting)
        db_session.commit()

    def test_distances_from_point(self, db_session, places, capsys):
        """
        GIVEN: A place representing a point
        WHEN:  A spatial query of US states is ordered by distance from that point
        THEN:  Returned state places should be ordered by their distance from that point
        """
        distance = func.ST_DistanceSphere(places["lake_placid_ny"].geometry, Place.geometry)
        states = db_session.query(Place).filter(Place.type == Place.STATE).order_by(
                distance).add_columns(distance).limit(4)

        # Note that we've limited it to 4 states, because MA has no geometry in the
        # places fixture, so it produces non-deterministic distances.
        expected = [('NY', 0), ('CT', 235), ('KS', 1818), ('NM', 2592)]
        actual = [(state.abbreviated_name, int(dist/1000)) for (state, dist) in list(states)]
        assert actual == expected

    def test_to_geojson(self, db_session, places):
        """
        GIVEN: Places that have been loaded from geojson
        WHEN:  They are passed as arguments to Place.to_geojson()
        THEN:  Their original geojson representation should be returned
        """
        TEST_DATA_DIR = Path(os.path.dirname(__file__)).parent / "data"
        zip_10018_geojson = (TEST_DATA_DIR / 'zip_10018_geojson.json').read_text()
        zip_11212_geojson = (TEST_DATA_DIR / 'zip_11212_geojson.json').read_text()

        # If you ask for the GeoJSON of one place, that place is returned as-is.
        geojson_single = Place.to_geojson(db_session, places["zip_10018"])
        assert geojson_single == json.loads(zip_10018_geojson)

        # If you ask for GeoJSON of several places, it's returned as a GeometryCollection document.
        geojson_multi = Place.to_geojson(db_session, places["zip_10018"], places["zip_11212"])
        assert geojson_multi['type'] == "GeometryCollection"

        # There are two geometries in this document -- one for each Place we passed in.
        assert len(geojson_multi['geometries']) == 2

        for geojson in [zip_10018_geojson, zip_11212_geojson]:
            assert json.loads(geojson) in geojson_multi['geometries']

    @pytest.mark.parametrize(
        "place_name,centroid_string",
        [
            pytest.param("new_york_state", "POINT(-75.503116 42.940380)", id="new_york_state"),
            pytest.param("connecticut_state", "POINT(-72.725708 41.620274)", id="connecticut_state"),
            pytest.param("kansas_state", "POINT(-98.3802053 38.484701)", id="kansas_state"),
            pytest.param("new_mexico_state", "POINT(-106.107840 34.421558)", id="new_mexico_state"),
            pytest.param("new_york_city", "POINT(-73.924869 40.694272)", id="new_york_city"),
            pytest.param("crude_kings_county", "POINT(-73.941020 40.640365)", id="crude_kings_county"),
            pytest.param("lake_placid_ny", "POINT(-73.59 44.17)", id="lake_placid_ny"),
            pytest.param("crude_new_york_county", "POINT(-73.968863 40.779112)", id="crude_new_york_county"),
            pytest.param("zip_10018", "POINT(-73.993192 40.755335)", id="zip_10018"),
            pytest.param("zip_11212", "POINT(-73.913026 40.662926)", id="zip_11212"),
            pytest.param("zip_12601", "POINT(-73.911652 41.703563)", id="zip_12601"),
            pytest.param("crude_albany", "POINT(-73.805886 42.675764)", id="crude_albany"),
            pytest.param("boston_ma", "POINT(-71.083837 42.318914)", id="boston_ma"),
            pytest.param("manhattan_ks", "POINT(-96.605011 39.188330)", id="manhattan_ks"),
        ]
    )
    def test_as_centroid_point(self, places, place_name, centroid_string):
        """
        GIVEN: A Place object with a defined geometry
        WHEN:  .as_centroid_point() is called on that Place instance
        THEN:  An EWKT Point string matching the centroid of that geometry should be returned
        """
        assert Location(places[place_name].as_centroid_point()) == Location(centroid_string)

    def test_overlaps_not_counting_border(self, db_session, places):
        """
        Test that overlaps_not_counting_border does not count places that share a border as
        intersecting, the way the PostGIS 'intersect' logic does.
        """

        def s_i(place1, place2):
            """
            Use overlaps_not_counting_border to provide a boolean answer
            to the question: does place 2 strictly intersect place 1?
            """
            qu = db_session.query(Place)
            qu = place1.overlaps_not_counting_border(qu)
            return place2 in qu.all()

        # Places that contain each other intersect.
        assert s_i(places["new_york_city"], places["new_york_state"]) is True
        assert s_i(places["new_york_state"], places["new_york_city"]) is True

        # Places that don't share a border don't intersect.
        assert s_i(places["new_york_city"], places["connecticut_state"]) is False
        assert s_i(places["connecticut_state"], places["new_york_city"]) is False

        # Connecticut and New York share a border, so PostGIS says they
        # intersect, but they don't "intersect" in the everyday sense,
        # so overlaps_not_counting_border excludes them.
        assert s_i(places["new_york_state"], places["connecticut_state"]) is False
        assert s_i(places["connecticut_state"], places["new_york_state"]) is False

    def test_parse_name(self):
        assert Place.parse_name("Kern County") == ("Kern", Place.COUNTY)
        assert Place.parse_name("New York State") == ("New York", Place.STATE)
        assert Place.parse_name("Chicago, IL") == ("Chicago, IL", None)

    def test_name_parts(self):
        assert Place.name_parts("Boston, MA") == ["MA", "Boston"]
        assert Place.name_parts("Boston, MA,") == ["MA", "Boston"]
        assert Place.name_parts("Anytown, USA") == ["USA", "Anytown"]
        assert Place.name_parts("Lake County, Ohio, US") == ["US", "Ohio", "Lake County"]

    def test_human_friendly_name_everywhere(self, db_session, create_test_place):
        """
        GIVEN: A Place instance whose type is Place.EVERYWHERE
        WHEN:  The .human_friendly_name property of that instance is accessed
        THEN:  None should be returned
        """
        everywhere = create_test_place(db_session, place_type=Place.EVERYWHERE)
        assert everywhere.human_friendly_name is None
        db_session.delete(everywhere)
        db_session.commit()

    def test_human_friendly_name_simple(self, db_session, create_test_place):
        """
        GIVEN: A Place instance with no parent, that is not Everywhere
        WHEN:  The .human_friendly_name property of that instance is accessed
        THEN:  The external_name attribute of the instance should be returned
        """
        for place_type in [
            Place.NATION, Place.STATE, Place.COUNTY, Place.CITY,
            Place.POSTAL_CODE, Place.LIBRARY_SERVICE_AREA
        ]:
            expected = str(uuid.uuid4())
            p = create_test_place(db_session, external_name=expected)
            assert p.human_friendly_name == expected
            db_session.delete(p)
        db_session.commit()

    def test_human_friendly_name_city_with_parent_hierarchy(self, db_session, create_test_place):
        """
        GIVEN: A Place instance with
                - a type of Place.CITY
                - an ancestor of type Place.STATE
        WHEN:  The .human_friendly_name property of that instance is accessed
        THEN:  A string should be returned that concatenates
                - the external name of the city
                - the string ', '
                - if defined, the abbreviated name of the STATE ancestor, or its external name
        """
        us = create_test_place(db_session, external_name='USA', place_type=Place.NATION)
        assert us.human_friendly_name == 'USA'

        georgia = create_test_place(
            db_session,
            external_name='Georgia',
            place_type=Place.STATE,
            parent=us
        )
        assert georgia.human_friendly_name == 'Georgia'

        fulton_county = create_test_place(
            db_session,
            external_name='Fulton',
            place_type=Place.COUNTY,
            parent=georgia
        )
        assert fulton_county.human_friendly_name == 'Fulton County, Georgia'

        atlanta = create_test_place(
            db_session,
            external_name='Atlanta',
            place_type=Place.CITY,
            parent=fulton_county
        )
        assert atlanta.human_friendly_name == 'Atlanta, Georgia'
        georgia.abbreviated_name = 'GA'
        assert fulton_county.human_friendly_name == 'Fulton County, GA'
        assert atlanta.human_friendly_name == 'Atlanta, GA'

        for db_item in [us, georgia, fulton_county, atlanta]:
            db_session.delete(db_item)
        db_session.commit()

    def test_human_friendly_name_county_with_parent_hierarchy(self, db_session, create_test_place):
        """
        GIVEN: A Place instance with
                - a type of Place.COUNTY
                - an ancestor of type Place.STATE
        WHEN:  The .human_friendly_name property of that instance is accessed
        THEN:  A string should be returned that concatenates
                - the external name of the county
                - the string '<county_word>, ', where 'county_word' is 'County' or 'Parish', as appropriate
                - if defined, the abbreviated name of the STATE ancestor, or its external name
        """
        us = create_test_place(db_session, external_name='USA', place_type=Place.NATION)
        assert us.human_friendly_name == 'USA'

        georgia = create_test_place(
            db_session,
            external_name='Georgia',
            place_type=Place.STATE,
            parent=us
        )
        assert georgia.human_friendly_name == 'Georgia'

        fulton_county = create_test_place(
            db_session,
            external_name='Fulton',
            place_type=Place.COUNTY,
            parent=georgia
        )
        assert fulton_county.human_friendly_name == 'Fulton County, Georgia'
        georgia.abbreviated_name = 'GA'
        assert fulton_county.human_friendly_name == 'Fulton County, GA'

        # Test the case where the county word is already in the external name
        fulton_county.external_name = 'Fulton County'
        assert fulton_county.human_friendly_name == 'Fulton County, GA'

        # Test for Louisiana and 'Parish'
        louisiana = create_test_place(
            db_session,
            external_name='Louisiana',
            place_type=Place.STATE,
            parent=us
        )
        natchitoches_parish = create_test_place(
            db_session,
            external_name='Natchitoches',
            place_type=Place.COUNTY,
            parent=louisiana
        )
        assert natchitoches_parish.human_friendly_name == 'Natchitoches Parish, Louisiana'

        for db_item in [us, georgia, fulton_county, louisiana, natchitoches_parish]:
            db_session.delete(db_item)
        db_session.commit()

    def test_hierarchy(self, db_session, create_test_place):
        """
        GIVEN: A Place instance
        WHEN:  The .hierarchy property is accessed
        THEN:  A list containing the parentage hierarchy, in [parent, grandparent, great-grandparent...]
                order is returned, or an empty list if the current instance has no parent.
        """
        us = create_test_place(db_session, external_name='USA', place_type=Place.NATION)
        assert us.hierarchy == []
        georgia = create_test_place(
            db_session,
            external_name='Georgia',
            place_type=Place.STATE,
            parent=us
        )
        assert georgia.hierarchy == [us]
        fulton_county = create_test_place(
            db_session,
            external_name='Fulton',
            place_type=Place.COUNTY,
            parent=georgia
        )
        assert fulton_county.hierarchy == [georgia, us]
        atlanta = create_test_place(
            db_session,
            external_name='Atlanta',
            place_type=Place.CITY,
            parent=fulton_county
        )
        assert atlanta.hierarchy == [fulton_county, georgia, us]

        for db_item in [us, georgia, fulton_county, atlanta]:
            db_session.delete(db_item)
        db_session.commit()

    def test_lookup_by_name(self, db_session, create_test_place):
        santa_barbara_city = create_test_place(db_session, external_name="Santa Barbara", place_type=Place.CITY)
        santa_barbara_county = create_test_place(db_session, external_name="Santa Barbara", place_type=Place.COUNTY)

        # Look up by name returns the city
        assert Place.lookup_by_name(db_session, "Santa Barbara").all() == [santa_barbara_city]

        # To find the county, must include 'County' in the name
        assert Place.lookup_by_name(db_session, "Santa Barbara County").all() == [santa_barbara_county]

    def test_lookup_inside(self, db_session, places, create_test_place):
        us = places["crude_us"]
        zip_10018 = places["zip_10018"]
        nyc = places["new_york_city"]
        new_york = places["new_york_state"]
        connecticut = places["connecticut_state"]
        manhattan_ks = places["manhattan_ks"]
        kings_county = places["crude_kings_county"]
        zip_12601 = places["zip_12601"]

        # In most cases, we want to test that both versions of lookup_inside() return the same result.
        def lookup_both_ways(parent, name, expect):
            assert parent.lookup_inside(name, using_overlap=True) == expect
            assert parent.lookup_inside(name, using_overlap=False) == expect

        everywhere = Place.everywhere(db_session)
        lookup_both_ways(everywhere, "US", us)
        lookup_both_ways(everywhere, "NY", new_york)
        lookup_both_ways(us, "NY", new_york)

        lookup_both_ways(new_york, "10018", zip_10018)
        lookup_both_ways(us, "10018, NY", zip_10018)
        lookup_both_ways(us, "New York, NY", nyc)
        lookup_both_ways(new_york, "New York", nyc)

        # Test that the disambiguators "State" and "County" are handled properly.
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

        # Even though the parent of a ZIP code is a state, special code allows you to look them up within the nation.
        lookup_both_ways(us, "10018", zip_10018)
        lookup_both_ways(new_york, "10018", zip_10018)

        # You can't find a place 'inside' itself.
        lookup_both_ways(us, "US", None)
        lookup_both_ways(new_york, "NY, US, 10018", None)

        # Or 'inside' a place that's known to be smaller than it.
        lookup_both_ways(kings_county, "NY", None)
        lookup_both_ways(us, "NY, 10018", None)
        lookup_both_ways(zip_10018, "NY", None)

        # There is a limited ability to look up places even when the name of the city is not in the database -- a
        # representative postal code is returned. This goes through lookup_one_through_external_source, which is
        # tested in more detail below.
        lookup_both_ways(new_york, "Poughkeepsie", zip_12601)

        # Now test cases where using_overlap makes a difference.
        #
        # First, the cases where using_overlap=True performs better.

        # Looking up the name of a county by itself only works with using_overlap=True, because the .parent of
        # a county is its state, not the US.
        #
        # Many county names are ambiguous, but this lets us parse the ones that are not.
        assert everywhere.lookup_inside("Kings County, US", using_overlap=True) == kings_county

        # Neither of these is obviously better.
        assert us.lookup_inside("Manhattan") is None
        with pytest.raises(MultipleResultsFound) as exc:
            us.lookup_inside("Manhattan", using_overlap=True)
        assert "More than one place called Manhattan inside United States." in str(exc.value)

        # Now the cases where using_overlap=False performs better.

        # "New York, US" is a little ambiguous, but they probably mean the state.
        assert us.lookup_inside("New York") == new_york
        with pytest.raises(MultipleResultsFound) as exc:
            us.lookup_inside("New York", using_overlap=True)
        assert "More than one place called New York inside United States." in str(exc.value)

        # "New York, New York" can only be parsed by parentage.
        assert us.lookup_inside("New York, New York") == nyc
        with pytest.raises(MultipleResultsFound) as exc:
            us.lookup_inside("New York, New York", using_overlap=True)
        assert "More than one place called New York inside United States." in str(exc.value)

        # Using geographic overlap has another problem -- although the name of the method is 'lookup_inside',
        # we're actually checking for _intersection_. Places that overlap are treated as being inside *each other*.
        assert zip_10018.lookup_inside("New York", using_overlap=True) == nyc
        assert zip_10018.lookup_inside("New York", using_overlap=False) is None

    def test_lookup_one_through_external_source(self, places, db_session, create_test_place):
        # We're going to find the approximate location of Poughkeepsie even though the database doesn't have
        # a Place named "Poughkeepsie".
        #
        # We're able to do this because uszipcode knows which ZIP codes are in Poughkeepsie, and we do have
        # a Place for one of those ZIP codes.
        zip_12601 = places["zip_12601"]
        new_york = places["new_york_state"]
        connecticut = places["connecticut_state"]

        m = new_york.lookup_one_through_external_source
        poughkeepsie_zips = m("Poughkeepsie")

        # There are three ZIP codes in Poughkeepsie, and uszipcode knows about all of them, but the only Place
        # returned by lookup_through_external_source is the one for the ZIP code we know about.
        assert poughkeepsie_zips == zip_12601

        # If we ask about a real place but there is no corresponding postal code Place in the database, we get nothing.
        assert m("Woodstock") is None

        # Similarly if we ask about a nonexistent place.
        assert m("ZXCVB") is None

        # Or if we try to use uszipcode on a place that's not in the US.
        ontario = create_test_place(db_session, external_id='35', external_name='Ontario',
                                    place_type=Place.STATE, abbreviated_name='ON', parent=None, geometry=None)
        assert ontario.lookup_one_through_external_source('Hamilton') is None

        # Calling this method on a Place that's not a state doesn't make sense (because uszipcode only knows about
        # cities within states), and the result is always None.
        assert zip_12601.lookup_one_through_external_source("Poughkeepsie") is None

        # lookup_one_through_external_source operates on the same rules as lookup_inside -- the city you're looking
        # up must be geographically inside the Place whose method you're calling.
        assert connecticut.lookup_one_through_external_source("Poughkeepsie") is None

    def test_served_by(self, places, libraries):
        zip = places["zip_10018"]
        nyc = places["new_york_city"]
        new_york = places["new_york_state"]
        connecticut = places["connecticut_state"]

        # There are two libraries here...
        nypl = libraries["nypl"]
        ct_state = libraries["connecticut_state_library"]

        # ...but only one serves the 10018 ZIP code.
        assert zip.served_by().all() == [nypl]

        assert nyc.served_by().all() == [nypl]
        assert connecticut.served_by().all() == [ct_state]

        # New York and Connecticut share a border, and the Connecticut state library serves the entire state,
        # including the border. Internally, we use overlaps_not_counting_border() to avoid concluding that the
        # Connecticut state library serves New York.
        assert new_york.served_by().all() == [nypl]


class TestPlaceAliasModel:
    """
    Currently the PlaceAlias model is simple enough not to need unit tests.
    """


class TestServiceAreaModel:
    """
    Currently the ServiceArea model is simple enough not to need unit tests.
    """
