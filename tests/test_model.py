from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import MultipleResultsFound
import base64
import datetime
import json
import operator
import random

import pytest

from config import Configuration
from emailer import Emailer
from model import (
    create,
    get_one,
    get_one_or_create,
    Admin,
    Audience,
    CollectionSummary,
    ConfigurationSetting,
    DelegatedPatronIdentifier,
    ExternalIntegration,
    Hyperlink,
    Library,
    LibraryAlias,
    Place,
    PlaceAlias,
    Validation,
)
from util import (
    GeometryUtility
)

from . import (
    DatabaseTest,
)


class TestPlace(DatabaseTest):

    def test_creation(self):
        # Create some US states represented by points.
        # (Rather than by multi-polygons, as they will be represented in
        # the actual application.)
        new_york, is_new = get_one_or_create(
            self._db, Place, type=Place.STATE, external_id='04',
            external_name='New York',
            create_method_kwargs=dict(geometry='SRID=4326;POINT(-75 43)')
        )
        assert is_new is True

        new_mexico, is_new = get_one_or_create(
            self._db, Place, type=Place.STATE, external_id='21',
            external_name='New Mexico',
            create_method_kwargs=dict(geometry='SRID=4326;POINT(-106 34)')
        )

        connecticut, is_new = get_one_or_create(
            self._db, Place, type=Place.STATE, external_id='14',
            external_name='Connecticut',
            create_method_kwargs=dict(geometry='SRID=4326;POINT(-73.7 41.6)')
        )

        # Create a city within one of the states, again represented by
        # a point rather than an outline.
        lake_placid, is_new = get_one_or_create(
            self._db, Place, type=Place.CITY, external_id='1234',
            external_name='Lake Placid',
            parent=new_york,
            create_method_kwargs=dict(
                geometry='SRID=4326;POINT(-73.59 44.17)'
            )
        )
        assert lake_placid.parent == new_york
        assert new_york.children == [lake_placid]
        assert new_mexico.children == []

        # Query the database to find states ordered by distance from
        # Lake Placid.
        distance = func.ST_DistanceSphere(
            lake_placid.geometry, Place.geometry
        )
        places = self._db.query(Place).filter(
            Place.type==Place.STATE).order_by(distance).add_columns(distance)

        # We can find the distance in kilometers between the 'Lake
        # Placid' point and the points representing the other states.
        assert [(x[0].external_name, int(x[1]/1000)) for x in places] == [("New York", 172), ("Connecticut", 285), ("New Mexico", 2993)]

    def test_aliases(self):
        new_york, is_new = get_one_or_create(
            self._db, Place, type=Place.STATE, external_id='04',
            external_name='New York',
            create_method_kwargs=dict(geometry='SRID=4326;POINT(-75 43)')
        )
        alias, is_new = get_one_or_create(
            self._db, PlaceAlias, place=new_york,
            name='New York State', language='eng'
        )
        assert new_york.aliases == [alias]

    def test_default_nation(self):
        m = Place.default_nation

        # We start out with no default nation.
        assert m(self._db) is None

        # The abbreviation of the default nation is stored in the
        # DEFAULT_NATION_ABBREVIATION setting.
        setting = ConfigurationSetting.sitewide(
            self._db, Configuration.DEFAULT_NATION_ABBREVIATION
        )
        assert setting.value is None

        # Set the default nation to the United States.
        setting.value = self.crude_us.abbreviated_name
        assert m(self._db) == self.crude_us

        # If there's no nation with this abbreviation,
        # there is no default nation.
        setting.value = "LL"
        assert m(self._db) is None

    def test_to_geojson(self):

        # If you ask for the GeoJSON of one place, that place is
        # returned as-is.
        zip1 = self.zip_10018
        geojson = Place.to_geojson(self._db, zip1)
        assert json.loads(self.zip_10018_geojson) == geojson

        # If you ask for GeoJSON of several places, it's returned as a
        # GeometryCollection document.
        zip2 = self.zip_11212
        geojson = Place.to_geojson(self._db, zip1, zip2)
        assert geojson['type'] == "GeometryCollection"

        # There are two geometries in this document -- one for each
        # Place we passed in.
        geometries = geojson['geometries']
        assert len(geometries) == 2
        for check in [self.zip_10018_geojson, self.zip_11212_geojson]:
            assert json.loads(check) in geojson['geometries']

    def test_overlaps_not_counting_border(self):
        """Test that overlaps_not_counting_border does not count places
        that share a border as intersecting, the way the PostGIS
        'intersect' logic does.
        """
        nyc = self.new_york_city
        new_york = self.new_york_state
        connecticut = self.connecticut_state

        def s_i(place1, place2):
            """Use overlaps_not_counting_border to provide a boolean answer
            to the question: does place 2 strictly intersect place 1?
            """
            qu = self._db.query(Place)
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

    def test_human_friendly_name(self):
        # Places of different types are given good-looking
        # human-friendly names.

        nation = self._place(external_name="United States", type=Place.NATION)
        assert "United States" == nation.human_friendly_name

        state = self._place(external_name="Alabama", abbreviated_name="AL",
                            type=Place.STATE, parent=nation)
        assert "Alabama" == state.human_friendly_name

        city = self._place(external_name="Montgomery", type=Place.CITY, 
                           parent=state)
        assert "Montgomery, AL" == city.human_friendly_name

        county = self._place(external_name="Montgomery", type=Place.COUNTY, 
                             parent=state)
        assert "Montgomery County, AL" == county.human_friendly_name

        postal_code = self._place(external_name="36043", type=Place.POSTAL_CODE,
                                  parent=state)
        assert "36043" == postal_code.human_friendly_name

        # This shouldn't happen, but just in case: the state's full
        # name is used if it has no abbreviated name.
        state.abbreviated_name = None
        assert "Montgomery, Alabama" == city.human_friendly_name
        assert "Montgomery County, Alabama" == county.human_friendly_name

        # 'everywhere' is not a distinct place with a well-known name.
        everywhere = self._place(type=Place.EVERYWHERE)
        assert None == everywhere.human_friendly_name

    def test_lookup_by_name(self):

        # There are two places in California called 'Santa Barbara': a
        # city, and a county (which includes the city).
        sb_city = self._place(external_name="Santa Barbara", type=Place.CITY)
        sb_county = self._place(
            external_name="Santa Barbara", type=Place.COUNTY
        )

        # If we look up "Santa Barbara" by name, we get the city.
        m = Place.lookup_by_name
        assert m(self._db, "Santa Barbara").all() == [sb_city]

        # To get Santa Barbara County, we have to refer to
        # "Santa Barbara County"
        assert m(self._db, "Santa Barbara County").all() == [sb_county]
        
    def test_lookup_inside(self):
        us = self.crude_us
        zip_10018 = self.zip_10018
        nyc = self.new_york_city
        new_york = self.new_york_state
        connecticut = self.connecticut_state
        manhattan_ks = self.manhattan_ks
        kansas = manhattan_ks.parent
        kings_county = self.crude_kings_county
        zip_12601 = self.zip_12601

        # In most cases, we want to test that both versions of
        # lookup_inside() return the same result.
        def lookup_both_ways(parent, name, expect):
            assert expect == parent.lookup_inside(name, using_overlap=True)
            assert expect == parent.lookup_inside(name, using_overlap=False)

        everywhere = Place.everywhere(self._db)
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
        assert everywhere.lookup_inside("Kings County, US", using_overlap=True) == kings_county

        # Neither of these is obviously better.
        assert us.lookup_inside("Manhattan") is None
        with pytest.raises(MultipleResultsFound) as exc:
            us.lookup_inside("Manhattan", using_overlap=True)
        assert "More than one place called Manhattan inside United States." in str(exc.value)

        # Now the cases where using_overlap=False performs better.

        # "New York, US" is a little ambiguous, but they probably mean
        # the state.
        assert us.lookup_inside("New York") == new_york
        with pytest.raises(MultipleResultsFound) as exc:
            us.lookup_inside("New York", using_overlap=True)
        assert "More than one place called New York inside United States." in str(exc.value)

        # "New York, New York" can only be parsed by parentage.
        assert us.lookup_inside("New York, New York") == nyc
        with pytest.raises(MultipleResultsFound) as exc:
            us.lookup_inside("New York, New York", using_overlap=True)
        assert "More than one place called New York inside United States." in str(exc.value)

        # Using geographic overlap has another problem -- although the
        # name of the method is 'lookup_inside', we're actually
        # checking for _intersection_. Places that overlap are treated
        # as being inside *each other*.
        assert zip_10018.lookup_inside("New York", using_overlap=True) == nyc
        assert zip_10018.lookup_inside("New York", using_overlap=False) is None

    def test_lookup_one_through_external_source(self):
        # We're going to find the approximate location of Poughkeepsie
        # even though the database doesn't have a Place named
        # "Poughkeepsie".
        #
        # We're able to do this because uszipcode knows which ZIP
        # codes are in Poughkeepsie, and we do have a Place for one of
        # those ZIP codes.
        zip_12601 = self.zip_12601
        new_york = self.new_york_state
        connecticut = self.connecticut_state

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
        ontario = self._place('35', 'Ontario', Place.STATE,
                              'ON', None, None)
        assert ontario.lookup_one_through_external_source('Hamilton') is None

        # Calling this method on a Place that's not a state doesn't
        # make sense (because uszipcode only knows about cities within
        # states), and the result is always None.
        assert zip_12601.lookup_one_through_external_source("Poughkeepsie") is None

        # lookup_one_through_external_source operates on the same
        # rules as lookup_inside -- the city you're looking up must be
        # geographically inside the Place whose method you're calling.
        assert connecticut.lookup_one_through_external_source("Poughkeepsie") is None

    def test_served_by(self):
        zip = self.zip_10018
        nyc = self.new_york_city
        new_york = self.new_york_state
        connecticut = self.connecticut_state

        # There are two libraries here...
        nypl = self._library("New York Public Library", eligibility_areas=[nyc])
        ct_state = self._library(
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


class TestLibrary(DatabaseTest):

    def test_timestamp(self):
        """Timestamp gets automatically set on database commit."""
        nypl = self._library("New York Public Library")
        first_modified = nypl.timestamp
        now = datetime.datetime.utcnow()
        self._db.commit()
        assert (now-first_modified).seconds < 2

        nypl.opds_url = "http://library/"
        self._db.commit()
        assert nypl.timestamp > first_modified

    def test_short_name(self):
        lib = self._library("A Library")
        lib.short_name = 'abcd'
        assert lib.short_name == "ABCD"
        try:
            lib.short_name = 'ab|cd'
            raise Error("Expected exception not raised.")
        except ValueError as e:
            assert str(e) == 'Short name cannot contain the pipe character.'

    def test_for_short_name(self):
        assert Library.for_short_name(self._db, 'ABCD') is None
        lib = self._library("A Library")
        lib.short_name = 'ABCD'
        assert Library.for_short_name(self._db, 'ABCD') == lib

    def test_for_urn(self):
        assert Library.for_urn(self._db, 'ABCD') is None
        lib = self._library()
        assert Library.for_urn(self._db, lib.internal_urn) == lib

    def test_random_short_name(self):
        # First, try with no duplicate check.
        random.seed(42)
        name = Library.random_short_name()

        expect = 'UDAXIH'
        assert expect == name

        # Reset the random seed so the same name will be generated again.
        random.seed(42)
        # Create a duplicate_check implementation that claims QAHFTR
        # has already been used.
        def already_used(name):
            return name == expect
        name = Library.random_short_name(duplicate_check=already_used)

        # random_short_name now generates `expect`, but it's a
        # duplicate, so it tries again and generates a new string
        # which passes the already_used test.

        expect_next = "HEXDVX"
        assert expect_next == name

        # To avoid an infinite loop, we will stop trying and raise an
        # exception after a certain number of attempts (the default is
        # 20).
        def theyre_all_duplicates(name):
            return True
        with pytest.raises(ValueError) as exc:
            Library.random_short_name(duplicate_check=theyre_all_duplicates)
        assert "Could not generate random short name after 20 attempts!" in str(exc.value)

    def test_set_library_stage(self):
        lib = self._library()

        # We can't change library_stage because only the registry can
        # take a library from production to non-production.
        def crash():
            lib.library_stage = Library.TESTING_STAGE
        with pytest.raises(ValueError) as exc:
            crash()
        assert "This library is already in production" in str(exc.value)

        # Have the registry take the library out of production.
        lib.registry_stage = Library.CANCELLED_STAGE
        assert lib.in_production is False

        # Now we can change the library stage however we want.
        lib.library_stage = Library.TESTING_STAGE
        lib.library_stage = Library.CANCELLED_STAGE
        lib.library_stage = Library.PRODUCTION_STAGE

    def test_in_production(self):
        lib = self._library()

        # The testing code creates a library that starts out in
        # production.
        assert lib.library_stage == Library.PRODUCTION_STAGE
        assert lib.registry_stage == Library.PRODUCTION_STAGE
        assert lib.in_production is True

        # If either library_stage or registry stage is not
        # PRODUCTION_STAGE, we are not in production.
        lib.registry_stage = Library.CANCELLED_STAGE
        assert lib.in_production is False

        lib.library_stage = Library.CANCELLED_STAGE
        assert lib.in_production is False

        lib.registry_stage = Library.PRODUCTION_STAGE
        assert lib.in_production is False

    def test_number_of_patrons(self):
        production_library = self._library()
        assert production_library.number_of_patrons == 0
        identifier1, is_new = DelegatedPatronIdentifier.get_one_or_create(
            self._db, production_library, self._str, DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID,
            None
        )
        assert production_library.number_of_patrons == 1
        # Identifiers that aren't Adobe Account IDs don't count towards the total.
        identifier2, is_new = DelegatedPatronIdentifier.get_one_or_create(
            self._db, production_library, self._str, "abc", None
        )
        assert production_library.number_of_patrons == 1
        # Identifiers can't be assigned to libraries that aren't in production.
        testing_library = self._library(library_stage=Library.TESTING_STAGE)
        assert testing_library.number_of_patrons == 0
        identifier3, is_new = DelegatedPatronIdentifier.get_one_or_create(
            self._db, testing_library, self._str, DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID,
            None
        )
        assert testing_library.number_of_patrons == 0

    def test__feed_restriction(self):
        """Test the _feed_restriction helper method."""

        def feed(production=True):
            """Find only libraries that belong in a certain feed."""
            qu = self._db.query(Library)
            qu = qu.filter(Library._feed_restriction(production))
            return qu.all()

        # This library starts out in production.
        library = self._library()

        # It shows up in both the production and testing feeds.
        for production in (True, False):
            assert feed(production) == [library]

        # Now one party thinks the library is in the testing stage.
        library.registry_stage = Library.TESTING_STAGE

        # It shows up in the testing feed but not the production feed.
        assert feed(True) == []
        assert feed(False) == [library]

        library.library_stage = Library.TESTING_STAGE
        library.registry_stage = Library.PRODUCTION_STAGE
        assert feed(True) == []
        assert feed(False) == [library]

        # Now on party thinks the library is in the cancelled stage,
        # and it will not show up in eithre feed.
        library.library_stage = Library.CANCELLED_STAGE
        for production in (True, False):
            assert feed(production) == []

    def test_set_hyperlink(self):
        library = self._library()

        with pytest.raises(ValueError) as exc:
            library.set_hyperlink("rel")
        assert "No Hyperlink hrefs were specified" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            library.set_hyperlink(None, ["href"])
        assert "No link relation was specified" in str(exc.value)

        link, is_modified = library.set_hyperlink("rel", "href1", "href2")
        assert link.rel == "rel"
        assert link.href == "href1"
        assert is_modified is True

        # Calling set_hyperlink again does not modify the link
        # so long as the old href is still a possibility.
        link2, is_modified = library.set_hyperlink("rel", "href2", "href1")
        assert link2 == link
        assert link2.rel == "rel"
        assert link2.href == "href1"
        assert is_modified is False

        # If there is no way to keep a Hyperlink's href intact,
        # set_hyperlink will modify it.
        link3, is_modified = library.set_hyperlink("rel", "href2", "href3")
        assert link3 == link
        assert link3.rel == "rel"
        assert link3.href == "href2"
        assert is_modified is True

        # Under no circumstances will two hyperlinks for the same rel be
        # created for a given library.
        assert library.hyperlinks == [link3]

        # However, a library can have multiple hyperlinks to the same
        # Resource using different rels.
        link4, modified = library.set_hyperlink("rel2", "href2")
        assert link4.resource == link3.resource
        assert modified is True

        # And two libraries can link to the same Resource using the same
        # rel.
        library2 = self._library()
        link5, modified = library2.set_hyperlink("rel2", "href2")
        assert modified is True
        assert link5.library == library2
        assert link5.resource == link4.resource

    def test_get_hyperlink(self):
        library = self._library()
        link1, is_modified = library.set_hyperlink("contact_email", "contact_href")
        link2, is_modified = library.set_hyperlink("help_email", "help_href")

        contact_link = Library.get_hyperlink(library, "contact_email")
        assert link1 == contact_link

        help_link = Library.get_hyperlink(library, "help_email")
        assert help_link == link2

    def test_library_service_area(self):
        zip = self.zip_10018

        nypl = self._library("New York Public Library", eligibility_areas=[zip])
        [service_area] = nypl.service_areas
        assert service_area.place == zip
        assert service_area.library == nypl

    def test_service_area_name(self):

        # Gather a few focus areas; the details don't matter.
        zip = self.zip_10018
        nyc = self.new_york_city
        new_york = self.new_york_state

        # 'Everywhere' is not a place with a distinctive name, so throughout
        # this test it will be ignored.
        everywhere = Place.everywhere(self._db)

        library = self._library(
            "Internet Archive", eligibility_areas=[everywhere],
            focus_areas=[everywhere]
        )
        assert None == library.service_area_name

        # A library with a single eligibility area has a
        # straightforward name.
        library = self._library(
            "test library", eligibility_areas=[everywhere, new_york],
            focus_areas=[everywhere]
        )
        assert "New York" == library.service_area_name

        # If you somehow specify the same place twice, it's fine.
        library = self._library(
            "test library", eligibility_areas=[new_york, new_york],
            focus_areas=[everywhere]
        )
        assert "New York" == library.service_area_name

        # If the library has an eligibility area and a focus area,
        # the focus area takes precedence.
        library = self._library(
            "test library", eligibility_areas=[everywhere, new_york],
            focus_areas=[nyc, everywhere]
        )
        assert "New York, NY" == library.service_area_name

        # If there are multiple focus areas and one eligibility area,
        # we're back to using the focus area.
        library = self._library(
            "test library", eligibility_areas=[everywhere, new_york],
            focus_areas=[nyc, zip, everywhere]
        )
        assert "New York" == library.service_area_name

        # If there are multiple focus areas _and_ multiple eligibility areas,
        # there's no one string that describes the service area.
        library = self._library(
            "test library", eligibility_areas=[everywhere, new_york, zip],
            focus_areas=[nyc, zip, everywhere]
        )
        assert None == library.service_area_name

    def test_relevant_audience(self):
        research = self._library(
            "NYU Library", eligibility_areas=[self.new_york_city], focus_areas=[self.new_york_city],
            audiences=[Audience.RESEARCH],
        )
        public = self._library(
            "New York Public Library", eligibility_areas=[self.new_york_city], focus_areas=[self.new_york_city],
            audiences=[Audience.PUBLIC],
        )
        education = self._library(
            "School", eligibility_areas=[self.new_york_city], focus_areas=[self.new_york_city],
            audiences=[Audience.EDUCATIONAL_PRIMARY, Audience.EDUCATIONAL_SECONDARY],
        )
        self._db.flush()

        [(lib, s)] = Library.relevant(self._db, (40.65, -73.94), 'eng', audiences=[Audience.PUBLIC]).most_common()
        assert lib == public

        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (40.65, -73.94), 'eng', audiences=[Audience.RESEARCH]).most_common()
        assert lib1 == research
        assert lib2 == public

        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (40.65, -73.94), 'eng', audiences=[Audience.EDUCATIONAL_PRIMARY]).most_common()
        assert lib1 == education
        assert lib2 == public

    def test_relevant_collection_size(self):
        small = self._library(
            "Small Library", eligibility_areas=[self.new_york_city], focus_areas=[self.new_york_city]
        )
        CollectionSummary.set(small, "eng", 10)
        large = self._library(
            "Large Library", eligibility_areas=[self.new_york_city], focus_areas=[self.new_york_city]
        )
        CollectionSummary.set(large, "eng", 100000)
        empty = self._library(
            "Empty Library", eligibility_areas=[self.new_york_city], focus_areas=[self.new_york_city]
        )
        CollectionSummary.set(empty, "eng", 0)
        unknown = self._library(
            "Unknown Library", eligibility_areas=[self.new_york_city], focus_areas=[self.new_york_city]
        )
        self._db.flush()

        [(lib1, s1), (lib2, s2), (lib3, s3)] = Library.relevant(self._db, (40.65, -73.94), 'eng').most_common()
        assert lib1 == large
        assert lib2 == small
        assert lib3 == unknown
        # Empty isn't included because we're sure it has no books in English.

    def test_relevant_eligibility_area(self):
        # Create two libraries. One serves New York City, and one serves
        # the entire state of Connecticut. They have the same focus area
        # so this only tests eligibility area.
        nypl = self._library(
            "New York Public Library", eligibility_areas=[self.new_york_city], focus_areas=[self.new_york_city, self.connecticut_state],
        )
        ct_state = self._library(
            "Connecticut State Library", eligibility_areas=[self.connecticut_state], focus_areas=[self.new_york_city, self.connecticut_state],
        )
        self._db.flush()

        # From this point in Brooklyn, NYPL is the closest library.
        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (40.65, -73.94), 'eng').most_common()
        assert lib1 == nypl
        assert lib2 == ct_state

        # From this point in Connecticut, CT State is the closest.
        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (41.3, -73.3), 'eng').most_common()
        assert lib1 == ct_state
        assert lib2 == nypl

        # From this point in New Jersey, NYPL is closest.
        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (40.72, -74.47), 'eng').most_common()
        assert lib1 == nypl
        assert lib2 == ct_state

        # From this point in the Indian Ocean, both libraries
        # are so far away they're below the score threshold.
        assert list(Library.relevant(self._db, (-15, 91), 'eng').most_common()) == []

    def test_relevant_focus_area(self):
        # Create two libraries. One serves New York City, and one serves
        # the entire state of Connecticut. They have the same eligibility
        # area, so this only tests focus area.
        nypl = self._library(
            "New York Public Library", focus_areas=[self.new_york_city], eligibility_areas=[self.new_york_city, self.connecticut_state]
        )
        ct_state = self._library(
            "Connecticut State Library", focus_areas=[self.connecticut_state], eligibility_areas=[self.new_york_city, self.connecticut_state]
        )
        self._db.flush()

        # From this point in Brooklyn, NYPL is the closest library.
        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (40.65, -73.94), 'eng').most_common()
        assert lib1 == nypl
        assert lib2 == ct_state

        # From this point in Connecticut, CT State is the closest.
        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (41.3, -73.3), 'eng').most_common()
        assert lib1 == ct_state
        assert lib2 == nypl

        # From this point in New Jersey, NYPL is closest.
        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (40.72, -74.47), 'eng').most_common()
        assert lib1 == nypl
        assert lib2 == ct_state

        # From this point in the Indian Ocean, both libraries
        # are so far away they're below the score threshold.
        assert list(Library.relevant(self._db, (-15, 91), 'eng').most_common()) == []

    def test_relevant_focus_area_size(self):
        # This library serves NYC.
        nypl = self._library(
            "New York Public Library", focus_areas=[self.new_york_city], eligibility_areas=[self.new_york_state]
        )
        # This library serves New York state.
        ny_state = self._library(
            "New York State Library", focus_areas=[self.new_york_state], eligibility_areas=[self.new_york_state]
        )
        self._db.flush()

        # This point in Brooklyn is in both libraries' focus areas,
        # but NYPL has a smaller focus area so it wins.
        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (40.65, -73.94), 'eng').most_common()
        assert lib1 == nypl
        assert lib2 == ny_state

    def test_relevant_library_with_no_service_areas(self):
        # Make sure a library with no service areas doesn't crash the query.

        # This library serves NYC.
        nypl = self._library(
            "New York Public Library", focus_areas=[self.new_york_city], eligibility_areas=[self.new_york_state]
        )
        # This library has no service areas.
        no_service_area = self._library(
            "Nowhere Library"
        )

        self._db.flush()

        [(lib, s)] = Library.relevant(self._db, (40.65, -73.94), 'eng').most_common()
        assert lib == nypl

    def test_relevant_all_factors(self):
        # This library serves the general public in NY state, with a focus on Manhattan.
        nypl = self._library(
            "New York Public Library", focus_areas=[self.crude_new_york_county],
            eligibility_areas=[self.new_york_state], audiences=[Audience.PUBLIC],
        )
        CollectionSummary.set(nypl, "eng", 150000)
        CollectionSummary.set(nypl, "spa", 20000)
        CollectionSummary.set(nypl, "rus", 5000)

        # This library serves the general public in NY state, with a focus on Brooklyn.
        bpl = self._library(
            "Brooklyn Public Library", focus_areas=[self.crude_kings_county],
            eligibility_areas=[self.new_york_state], audiences=[Audience.PUBLIC],
        )
        CollectionSummary.set(bpl, "eng", 75000)
        CollectionSummary.set(bpl, "spa", 10000)

        # This library serves the general public in Albany.
        albany = self._library(
            "Albany Public Library", focus_areas=[self.crude_albany],
            eligibility_areas=[self.crude_albany], audiences=[Audience.PUBLIC],
        )
        CollectionSummary.set(albany, "eng", 50000)
        CollectionSummary.set(albany, "spa", 5000)

        # This library serves NYU students.
        nyu_lib = self._library(
            "NYU Library", focus_areas=[self.new_york_city],
            eligibility_areas=[self.new_york_city], audiences=[Audience.EDUCATIONAL_SECONDARY],
        )
        CollectionSummary.set(nyu_lib, "eng", 100000)

        # These libraries serves the general public, but mostly academics.
        nyu_press = self._library(
            "NYU Press", focus_areas=[self.new_york_city],
            eligibility_areas=[Place.everywhere(self._db)], audiences=[Audience.RESEARCH, Audience.PUBLIC],
        )
        CollectionSummary.set(nyu_press, "eng", 40)

        unm = self._library(
            "UNM Press", focus_areas=[self.kansas_state],
            eligibility_areas=[Place.everywhere(self._db)], audiences=[Audience.RESEARCH, Audience.PUBLIC],
        )
        CollectionSummary.set(unm, "eng", 60)
        CollectionSummary.set(unm, "spa", 10)

        # This library serves people with print disabilities in the US.
        bard = self._library(
            "BARD", focus_areas=[self.crude_us],
            eligibility_areas=[self.crude_us], audiences=[Audience.PRINT_DISABILITY],
        )
        CollectionSummary.set(bard, "eng", 100000)

        # This library serves the general public everywhere.
        internet_archive = self._library(
            "Internet Archive", focus_areas=[Place.everywhere(self._db)],
            eligibility_areas=[Place.everywhere(self._db)], audiences=[Audience.PUBLIC],
        )
        CollectionSummary.set(internet_archive, "eng", 10000000)
        CollectionSummary.set(internet_archive, "spa", 1000)
        CollectionSummary.set(internet_archive, "rus", 1000)

        self._db.flush()

        # In Manhattan.
        libraries = Library.relevant(self._db, (40.75, -73.98), "eng").most_common()
        assert len(libraries) == 4
        assert [l[0] for l in libraries] == [nypl, bpl, internet_archive, nyu_press]
            

        # In Brooklyn.
        libraries = Library.relevant(self._db, (40.65, -73.94), "eng").most_common()
        assert len(libraries) == 4
        assert [l[0] for l in libraries] == [bpl, nypl, internet_archive, nyu_press]

        # In Queens.
        libraries = Library.relevant(self._db, (40.76, -73.91), "eng").most_common()
        assert len(libraries) == 4
        assert [l[0] for l in libraries] == [nypl, bpl, internet_archive, nyu_press]

        # In Albany.
        libraries = Library.relevant(self._db, (42.66, -73.77), "eng").most_common()
        assert len(libraries) == 5
        assert [l[0] for l in libraries] == [albany, nypl, bpl, internet_archive, nyu_press]
            

        # In Syracuse (200km west of Albany).
        libraries = Library.relevant(self._db, (43.06, -76.15), "eng").most_common()
        assert len(libraries) == 4
        assert [l[0] for l in libraries] == [nypl, bpl, internet_archive, nyu_press]

        # In New Jersey.
        libraries = Library.relevant(self._db, (40.79, -74.43), "eng").most_common()
        assert len(libraries) == 4
        assert [l[0] for l in libraries] == [nypl, bpl, internet_archive, nyu_press]

        # In Las Cruces, NM. Internet Archive is first at the moment
        # due to its large collection, but maybe it would be better if UNM was.
        libraries = Library.relevant(self._db, (32.32, -106.77), "eng").most_common()
        assert len(libraries) == 2
        assert set([l[0] for l in libraries]) == set([unm, internet_archive])
            

        # Russian speaker in Albany. Albany doesn't pass the score threshold
        # since it didn't report having any Russian books, but maybe we should
        # consider the total collection size as well as the user's language.
        libraries = Library.relevant(self._db, (42.66, -73.77), "rus").most_common()
        assert len(libraries) == 2
        assert [l[0] for l in libraries] == [nypl, internet_archive]

        # Spanish speaker in Manhattan.
        libraries = Library.relevant(self._db, (40.75, -73.98), "spa").most_common()
        assert len(libraries) == 4
        assert [l[0] for l in libraries] == [nypl, bpl, internet_archive, unm]

        # Patron with a print disability in Manhattan.
        libraries = Library.relevant(self._db, (40.75, -73.98), "eng", audiences=[Audience.PRINT_DISABILITY]).most_common()
        assert len(libraries) == 5
        assert [l[0] for l in libraries] == [bard, nypl, bpl, internet_archive, nyu_press]

    def test_nearby(self):
        # Create two libraries. One serves New York City, and one serves
        # the entire state of Connecticut.
        nypl = self._library(
            "New York Public Library", eligibility_areas=[self.new_york_city]
        )
        ct_state = self._library(
            "Connecticut State Library", eligibility_areas=[self.connecticut_state]
        )

        # From this point in Brooklyn, NYPL is the closest library.
        # NYPL's service area includes that point, so the distance is
        # zero. The service area of CT State (i.e. the Connecticut
        # border) is only 44 kilometers away, so it also shows up.
        [(lib1, d1), (lib2, d2)] = Library.nearby(self._db, (40.65, -73.94))

        assert d1 == 0
        assert lib1 == nypl

        assert int(d2/1000) == 44
        assert lib2 == ct_state

        # From this point in Connecticut, CT State is the closest
        # library (0 km away), so it shows up first, but NYPL (61 km
        # away) also shows up as a possibility.
        [(lib1, d1), (lib2, d2)] = Library.nearby(self._db, (41.3, -73.3))
        assert lib1 == ct_state
        assert d1 == 0

        assert lib2 == nypl
        assert int(d2/1000) == 61

        # From this point in Pennsylvania, NYPL shows up (142km away) but
        # CT State does not.
        [(lib1, d1)] = Library.nearby(self._db, (40, -75.8))
        assert lib1 == nypl
        assert int(d1/1000) == 142

        # If we only look within a 100km radius, then there are no
        # libraries near that point in Pennsylvania.
        assert Library.nearby(self._db, (40, -75.8), 100).all() == []

        # By default, nearby() only finds libraries that are in production.
        def m(production):
            return Library.nearby(
                self._db, (41.3, -73.3), production=production
            ).count()
        # Take all the libraries we found earlier out of production.
        for l in ct_state, nypl:
            l.registry_stage = Library.TESTING_STAGE
        # Now there are no results.
        assert m(True) == 0

        # But we can run a search that includes libraries in the TESTING stage.
        assert m(False) == 2

    def test_query_cleanup(self):
        m = Library.query_cleanup

        assert m("THE LIBRARY") == "the library"
        assert m("\tthe   library\n\n") == "the library"
        assert m("the libary") == "the library"

    def test_as_postal_code(self):
        m = Library.as_postal_code
        # US ZIP codes are recognized as postal codes.
        assert m("93203") == "93203"
        assert m("93203-1234") == "93203"
        assert m("the library") is None

        # A UK post code is not currently recognized.
        assert m("AB1 0AA") is None

    def test_query_parts(self):
        m = Library.query_parts
        assert m("93203") == (None, "93203", Place.POSTAL_CODE)
        assert m("new york public library") == ("new york public library", "new york", None)
        assert m("queens library") == ("queens library", "queens", None)
        assert m("kern county library") == ("kern county library", "kern", Place.COUNTY)
        assert m("new york state library") == ("new york state library", "new york", Place.STATE)
        assert m("lapl") == ("lapl", "lapl", None)

    def test_search_by_library_name(self):
        def search(name, here=None, **kwargs):
            return list(
                Library.search_by_library_name(self._db, name, here, **kwargs)
            )

        # The Brooklyn Public Library serves New York City.
        brooklyn = self._library(
            name="Brooklyn Public Library", focus_areas=[self.new_york_city, self.zip_11212]
        )

        # We can find the library by its name.
        assert search("brooklyn public library") == [brooklyn]

        # We can tolerate a small number of typos in a name or alias
        # that is longer than 6 characters.
        assert search("broklyn public library") == [brooklyn]
        get_one_or_create(
            self._db, LibraryAlias, name="Bklynlib", language=None,
            library=brooklyn
        )
        assert search("zklynlib") == [brooklyn]

        # The Boston Public Library serves Boston, MA.
        boston = self._library(
            name="Boston Public Library", focus_areas=[self.boston_ma]
        )

        # Searching for part of the name--i.e. "boston" rather than "boston public library"--should work.
        assert search("boston") == [boston]

        # Both libraries are known colloquially as 'BPL'.
        for library in (brooklyn, boston):
            get_one_or_create(
                self._db, LibraryAlias, name="BPL", language=None,
                library=library
            )
        assert set(search("bpl")) == set([brooklyn, boston]) 
        
        # We do not tolerate typos in short names, because the chance of
        # ambiguity is so high.
        assert search("opl") == []

        # If we're searching for "BPL" from California, Brooklyn shows
        # up first, because it's closer to California.
        assert [x[0].name for x in search("bpl", GeometryUtility.point(35, -118))] == ["Brooklyn Public Library", "Boston Public Library"]

        # If we're searching for "BPL" from Maine, Boston shows
        # up first, because it's closer to Maine.
        assert [x[0].name for x in search("bpl", GeometryUtility.point(43, -70))] == ["Boston Public Library", "Brooklyn Public Library"]

        # By default, search_by_library_name() only finds libraries
        # in production. Put them in the TESTING stage and they disappear.
        for l in (brooklyn, boston):
            l.registry_stage = Library.TESTING_STAGE
        assert search("bpl", production=True) == []

        # But you can find them by passing in production=False.
        assert len(search("bpl", production=False)) == 2

    def test_search_by_location(self):
        # We know about three libraries.
        nypl = self.nypl
        kansas_state = self.kansas_state_library
        connecticut_state = self.connecticut_state_library

        # The NYPL explicitly covers New York City, which has
        # 'Manhattan' as an alias.
        [nyc, zip_11212] = [x.place for x in nypl.service_areas]
        assert "Manhattan" in [x.name for x in nyc.aliases]

        # The Kansas state library covers the entire state,
        # which happens to contain a city called Manhattan.
        [kansas] = [x.place for x in kansas_state.service_areas]
        assert kansas.external_name == "Kansas"
        assert kansas.type == Place.STATE
        manhattan_ks = self.manhattan_ks

        # A search for 'manhattan' finds both libraries.
        libraries = list(Library.search_by_location_name(self._db, "manhattan"))
        assert set([x.name for x in libraries]) == set(["NYPL", "Kansas State Library"])

        # If you're searching from California, the Kansas library
        # shows up first.
        ca_results = Library.search_by_location_name(
            self._db, "manhattan", here=GeometryUtility.point(35, -118)
        )
        assert [x[0].name for x in ca_results] == ["Kansas State Library", "NYPL"]

        # If you're searching from Maine, the New York library shows
        # up first.
        me_results = Library.search_by_location_name(
            self._db, "manhattan", here=GeometryUtility.point(43, -70)
        )
        assert [x[0].name for x in me_results] == ["NYPL", "Kansas State Library"]

        # We can insist that only certain types of places be considered as
        # matching the name. There is no state called 'Manhattan', so
        # this query finds nothing.
        excluded = Library.search_by_location_name(
            self._db, "manhattan", type=Place.STATE
        )
        assert excluded.all() == []

        # A search for "Brooklyn" finds the NYPL, but it only finds it
        # once, even though NYPL is associated with two places called
        # "Brooklyn": New York City and the ZIP code 11212
        [brooklyn_results] = Library.search_by_location_name(
            self._db, "brooklyn", here=GeometryUtility.point(43, -70)
        )
        assert brooklyn_results[0] == nypl

        nypl.registry_stage = Library.TESTING_STAGE
        assert Library.search_by_location_name(self._db, "brooklyn", here=GeometryUtility.point(43, -70), production=True).all() == []
        
        assert Library.search_by_location_name(self._db, "brooklyn", here=GeometryUtility.point(43, -70), production=False).count() == 1
        
    def test_search_within_description(self):
        """Test searching for a phrase within a library's description."""
        library = self._library(
            name="Library With Description",
            description="We are giving this library a description for testing purposes."
        )
        results = list(Library.search_within_description(self._db, "testing purposes"))
        assert results == [library]

    def test_search(self):
        """Test the overall search method."""

        # Here's a Kansas library with a confusing name whose
        # Levenshtein distance from "New York" is 2.
        new_work = self._library(name="Now Work", focus_areas=[self.kansas_state])

        # Here's a library whose service area includes a place called
        # "New York".
        nypl = self.nypl

        libraries = Library.search(self._db, (40.7, -73.9), "NEW YORK")
        # Even though NYPL is closer to the current location, the
        # Kansas library showed up first because it was a name match,
        # as opposed to a service location match.
        assert [x[0].name for x in libraries] == ['Now Work', 'NYPL']
        assert [int(x[1]/1000) for x in libraries] == [1768, 0]

        # This search query has a Levenshtein distance of 1 from "New
        # York", but a distance of 3 from "Now Work", so only NYPL
        # shows up.
        #
        # Although "NEW YORM" matches both the city and state, both of
        # which intersect with NYPL's service area, NYPL only shows up
        # once.
        libraries = Library.search(self._db, (40.7, -73.9), "NEW YORM")
        assert [x[0].name for x in libraries] == ['NYPL']

        # Searching for a place name picks up libraries whose service
        # areas intersect with that place.
        libraries = Library.search(self._db, (40.7, -73.9), "Kansas")
        assert [x[0].name for x in libraries] == ['Now Work']

        # By default, search() only finds libraries in production.
        self.nypl.registry_stage = Library.TESTING_STAGE
        new_work.registry_stage = Library.TESTING_STAGE
        def m(production):
            return len(
                Library.search(
                    self._db, (40.7, -73.9), "New York", production
                )
            )
        assert m(True) == 0

        # But you can find libraries that are in the testing stage
        # by passing in production=False.
        assert m(False) == 2

    def test_search_excludes_duplicates(self):
        # Here's a library that serves a place called Kansas
        # whose name is also "Kansas"
        library = self._library(name="Kansas", focus_areas=[self.kansas_state])
        # It matches both the name search and the location search.
        assert Library.search_by_location_name(self._db, "kansas").all() == [library]
        assert Library.search_by_library_name(self._db, "kansas").all() == [library]

        # But when we do the general search, the library only shows up once.
        [(result, distance)] = Library.search(self._db, (0, 0), "Kansas")
        assert result == library


class TestCollectionSummary(DatabaseTest):

    def test_set(self):
        library = self._library()
        summary = CollectionSummary.set(library, "eng", 100)
        assert summary.library == library
        assert summary.language == "eng"
        assert summary.size == 100

        # Call set() again and we get the same object back.
        summary2 = CollectionSummary.set(library, "eng", "0")
        assert summary2 == summary
        assert summary.size == 0

    def test_unrecognized_language_is_set_as_unknown(self):
        library = self._library()
        summary = CollectionSummary.set(library, "mmmmmm", 100)
        assert summary.language is None
        assert summary.size == 100

    def test_size_must_be_integerable(self):
        library  = self._library()
        with pytest.raises(ValueError) as exc:
            CollectionSummary.set(library, "eng", "fruit")
        assert "invalid literal for" in str(exc.value)

    def test_negative_size_is_not_allowed(self):
        library  = self._library()
        with pytest.raises(ValueError) as exc:
            CollectionSummary.set(library, "eng", "-1")
        assert "Collection size cannot be negative." in str(exc.value)


class TestAudience(DatabaseTest):
    def test_unrecognized_audience(self):
        with pytest.raises(ValueError) as exc:
            Audience.lookup(self._db, "no such audience")
        assert "Unknown audience: no such audience" in str(exc.value)


class TestDelegatedPatronIdentifier(DatabaseTest):

    def test_get_one_or_create(self):
        library = self._library()
        patron_identifier = self._str
        identifier_type = DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID
        def make_id():
            return "id1"
        identifier, is_new = DelegatedPatronIdentifier.get_one_or_create(
            self._db, library, patron_identifier, identifier_type,
            make_id
        )
        assert is_new is True
        assert identifier.library == library
        assert identifier.patron_identifier == patron_identifier
        # id_1() was called.
        assert identifier.delegated_identifier == "id1"

        # Try the same thing again but provide a different create_function
        # that raises an exception if called.
        def explode():
            raise Exception("I should never be called.")
        identifier2, is_new = DelegatedPatronIdentifier.get_one_or_create(
            self._db, library, patron_identifier, identifier_type, explode
        )
        # The existing identifier was looked up.
        assert is_new is False
        assert identifier.id == identifier2.id
        # id_2() was not called.
        assert identifier2.delegated_identifier == "id1"

class TestExternalIntegration(DatabaseTest):

    def setup(self):
        super(TestExternalIntegration, self).setup()
        self.external_integration, ignore = create(
            self._db, ExternalIntegration, goal=self._str, protocol=self._str
        )

    def test_set_key_value_pair(self):
        """Test the ability to associate extra key-value pairs with
        an ExternalIntegration.
        """
        assert self.external_integration.settings == []

        setting = self.external_integration.set_setting("website_id", "id1")
        assert setting.key == "website_id"
        assert setting.value == "id1"

        # Calling set() again updates the key-value pair.
        assert self.external_integration.settings == [setting]
        setting2 = self.external_integration.set_setting("website_id", "id2")
        assert setting2 == setting
        assert setting2.value == "id2"

        assert self.external_integration.setting("website_id") == setting2

    def test_explain(self):
        integration, ignore = create(
            self._db, ExternalIntegration,
            protocol="protocol", goal="goal"
        )
        integration.name = "The Integration"
        integration.setting("somesetting").value = "somevalue"
        integration.setting("password").value = "somepass"

        expect = """ID: %s
Name: The Integration
Protocol/Goal: protocol/goal
somesetting='somevalue'""" % integration.id
        actual = integration.explain()
        assert expect == "\n".join(actual)

        # If we pass in True for include_secrets, we see the passwords.
        with_secrets = integration.explain(include_secrets=True)
        assert "password='somepass'" in with_secrets

class TestConfigurationSetting(DatabaseTest):

    def test_is_secret(self):
        """Some configuration settings are considered secrets,
        and some are not.
        """
        m = ConfigurationSetting._is_secret
        assert m('secret') is True
        assert m('password') is True
        assert m('its_a_secret_to_everybody') is True
        assert m('the_password') is True
        assert m('password_for_the_account') is True
        assert m('public_information') is False

        assert ConfigurationSetting.sitewide(self._db, "secret_key").is_secret is True
        assert ConfigurationSetting.sitewide(self._db, "public_key").is_secret is False

    def test_value_or_default(self):
        integration, ignore = create(
            self._db, ExternalIntegration, goal=self._str, protocol=self._str
        )
        setting = integration.setting("key")
        assert setting.value is None

        # If the setting has no value, value_or_default sets the value to
        # the default, and returns the default.
        assert setting.value_or_default("default value") == "default value"
        assert setting.value == "default value"

        # Once the value is set, value_or_default returns the value.
        assert setting.value_or_default("new default") == "default value"

        # If the setting has any value at all, even the empty string,
        # it's returned instead of the default.
        setting.value = ""
        assert setting.value_or_default("default") == ""

    def test_value_inheritance(self):

        key = "SomeKey"

        # Here's a sitewide configuration setting.
        sitewide_conf = ConfigurationSetting.sitewide(self._db, key)

        # Its value is not set.
        assert sitewide_conf.value is None

        # Set it.
        sitewide_conf.value = "Sitewide value"
        assert sitewide_conf.value == "Sitewide value"

        # Here's an integration, let's say the Adobe Vendor ID setup.
        adobe, ignore = create(
            self._db, ExternalIntegration,
            goal=ExternalIntegration.DRM_GOAL, protocol="Adobe Vendor ID"
        )

        # It happens to a ConfigurationSetting for the same key used
        # in the sitewide configuration.
        adobe_conf = ConfigurationSetting.for_externalintegration(key, adobe)

        # But because the meaning of a configuration key differ so
        # widely across integrations, the Adobe integration does not
        # inherit the sitewide value for the key.
        assert adobe_conf.value is None
        adobe_conf.value = "Adobe value"

        # Here's a library which has a ConfigurationSetting for the same
        # key used in the sitewide configuration.
        library = self._library()
        library_conf = ConfigurationSetting.for_library(key, library)

        # Since all libraries use a given ConfigurationSetting to mean
        # the same thing, a library _does_ inherit the sitewide value
        # for a configuration setting.
        assert library_conf.value == "Sitewide value"

        # Change the site-wide configuration, and the default also changes.
        sitewide_conf.value = "New site-wide value"
        assert library_conf.value == "New site-wide value"

        # The per-library value takes precedence over the site-wide
        # value.
        library_conf.value = "Per-library value"
        assert library_conf.value == "Per-library value"

        # Now let's consider a setting like on the combination of a library and an
        # integration integration.
        key = "patron_identifier_prefix"
        library_patron_prefix_conf = ConfigurationSetting.for_library_and_externalintegration(
            self._db, key, library, adobe
        )
        assert library_patron_prefix_conf.value is None

        # If the integration has a value set for this
        # ConfigurationSetting, that value is inherited for every
        # individual library that uses the integration.
        generic_patron_prefix_conf = ConfigurationSetting.for_externalintegration(
            key, adobe
        )
        assert generic_patron_prefix_conf.value is None
        generic_patron_prefix_conf.value = "Integration-specific value"
        assert library_patron_prefix_conf.value == "Integration-specific value"

        # Change the value on the integration, and the default changes
        # for each individual library.
        generic_patron_prefix_conf.value = "New integration-specific value"
        assert library_patron_prefix_conf.value == "New integration-specific value"

        # The library+integration setting takes precedence over the
        # integration setting.
        library_patron_prefix_conf.value = "Library-specific value"
        assert library_patron_prefix_conf.value == "Library-specific value"

    def test_duplicate(self):
        """You can't have two ConfigurationSettings for the same key,
        library, and external integration.

        (test_relationships shows that you can have two settings for the same
        key as long as library or integration is different.)
        """
        key = self._str
        integration, ignore = create(
            self._db, ExternalIntegration, goal=self._str, protocol=self._str
        )
        library = self._library()
        setting = ConfigurationSetting.for_library_and_externalintegration(
            self._db, key, library, integration
        )
        setting2 = ConfigurationSetting.for_library_and_externalintegration(
            self._db, key, library, integration
        )
        assert setting2 == setting
        with pytest.raises(IntegrityError):
            create(self._db, ConfigurationSetting, key=key, library_id=library.id, external_integration=integration)
        # We really screwed up the database session there -- roll it back
        # so that test cleanup can proceed.
        self._db.rollback()

    def test_int_value(self):
        number = ConfigurationSetting.sitewide(self._db, "number")
        assert number.int_value is None

        number.value = "1234"
        assert number.int_value == 1234

        number.value = "tra la la"
        with pytest.raises(ValueError):
            number.int_value

    def test_float_value(self):
        number = ConfigurationSetting.sitewide(self._db, "number")
        assert number.int_value is None

        number.value = "1234.5"
        assert number.float_value == 1234.5

        number.value = "tra la la"
        with pytest.raises(ValueError):
            number.float_value

    def test_json_value(self):
        jsondata = ConfigurationSetting.sitewide(self._db, "json")
        assert jsondata.int_value is None

        jsondata.value = "[1,2]"
        assert jsondata.json_value == [1,2]

        jsondata.value = "tra la la"
        with pytest.raises(ValueError):
            jsondata.json_value

    def test_explain(self):
        integration, ignore = create(
            self._db, ExternalIntegration,
            protocol="protocol", goal="goal"
        )
        integration.name = "The Integration"
        integration.setting("somesetting").value = "somevalue"
        integration.setting("password").value = "somepass"

        expect = (
            "ID: %s\n"
            "Name: The Integration\n"
            "Protocol/Goal: protocol/goal\n"
            "somesetting='somevalue'"
        )
        actual = integration.explain()
        assert "\n".join(actual) == expect % integration.id

        # If we pass in True for include_secrets, we see the passwords.
        with_secrets = integration.explain(include_secrets=True)
        assert "password='somepass'" in with_secrets


class TestHyperlink(DatabaseTest):

    def test_notify(self):
        class Mock(Emailer):
            sent = []
            url_for_calls = []

            def __init__(self):
                """We don't need any of the arguments that are required
                for the Emailer constructor.
                """

            def send(self, type, to_address, **kwargs):
                self.sent.append((type, to_address, kwargs))

            def url_for(self, controller, **kwargs):
                """Just a convenient place to mock Flask's url_for()."""
                self.url_for_calls.append((controller, kwargs))
                return "http://url/"

        emailer = Mock()

        ConfigurationSetting.sitewide(
            self._db, Configuration.REGISTRY_CONTACT_EMAIL
        ).value = "me@registry"

        library = self._library()
        library.web_url = "http://library/"
        link, is_modified = library.set_hyperlink(
            Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL, "mailto:you@library"
        )
        link.notify(emailer, emailer.url_for)

        # A Validation object was created for the Hyperlink.
        validation = link.resource.validation
        secret = validation.secret

        (type, sent_to, kwargs) = emailer.sent.pop()

        # We 'sent' an email about the fact that a new email address was
        # registered.
        assert type == emailer.ADDRESS_NEEDS_CONFIRMATION
        assert sent_to == "you@library"

        # These arguments were created to fill in the ADDRESS_NEEDS_CONFIRMATION
        # template.
        assert kwargs['registry_support'] == "me@registry"
        assert kwargs['email'] == "you@library"
        assert kwargs['rel_desc'] == "copyright designated agent"
        assert kwargs['library'] == library.name
        assert kwargs['library_web_url'] == library.web_url
        assert kwargs['confirmation_link'] == "http://url/"

        # url_for was called to create the confirmation link.
        controller, kwargs = emailer.url_for_calls.pop()
        assert controller == "confirm_resource"
        assert kwargs['secret'] == secret
        assert kwargs['resource_id'] == link.resource.id

        # If a Resource we already know about is associated with
        # a new Hyperlink, an ADDRESS_DESIGNATED email is sent instead.
        link2, is_modified = library.set_hyperlink("help", "mailto:you@library")
        link2.notify(emailer, emailer.url_for)

        (type, href, kwargs) = emailer.sent.pop()
        assert type == emailer.ADDRESS_DESIGNATED
        assert kwargs['rel_desc'] == "patron help contact address"

        # url_for was not called again, since an ADDRESS_DESIGNATED
        # email does not include a validation link.
        assert emailer.url_for_calls == []

        # And the Validation was not reset.
        assert link.resource.validation.secret == secret

        # Same if we somehow send another notification for a Hyperlink with an
        # active Validation.
        link.notify(emailer, emailer.url_for)
        (type, href, kwargs) = emailer.sent.pop()
        assert type == emailer.ADDRESS_DESIGNATED
        assert link.resource.validation.secret == secret

        # However, if a Hyperlink's Validation has expired, it's reset and a new
        # ADDRESS_NEEDS_CONFIRMATION email is sent out.
        now = datetime.datetime.utcnow()
        link.resource.validation.started_at = (now - datetime.timedelta(days=10))
        link.notify(emailer, emailer.url_for)
        (type, href, kwargs) = emailer.sent.pop()
        assert type == emailer.ADDRESS_NEEDS_CONFIRMATION
        assert 'confirmation_link' in kwargs

        # The Validation has been reset.
        assert link.resource.validation == validation
        assert validation.deadline > now
        assert secret != validation.secret


class TestValidation(DatabaseTest):
    """Test the Resource validation process."""

    def test_restart_validation(self):

        # This library has two links.
        library = self._library()
        link1, ignore = library.set_hyperlink("rel", "mailto:me@library.org")
        email = link1.resource
        link2, ignore = library.set_hyperlink("rel", "http://library.org")
        http = link2.resource

        # Let's set up validation for both of them.
        now = datetime.datetime.utcnow()
        email_validation = email.restart_validation()
        http_validation = http.restart_validation()

        for v in (email_validation, http_validation):
            assert (v.started_at - now).total_seconds() < 2
            assert v.secret is not None

        # A random secret was generated for each Validation.
        assert email_validation.secret != http_validation.secret

        # Let's imagine that validation succeeded and is being
        # invalidated for some reason.
        email_validation.success = True
        old_started_at = email_validation.started_at
        old_secret = email_validation.secret
        email_validation_2 = email.restart_validation()

        # Instead of a new Validation being created, the earlier
        # Validation has been invalidated.
        assert email_validation_2 == email_validation
        assert email_validation_2.success is False

        # The secret has changed.
        assert old_secret != email_validation.secret

    def test_mark_as_successful(self):

        validation, ignore = create(self._db, Validation)
        assert validation.active is True
        assert validation.success is False
        assert validation.secret is not None

        validation.mark_as_successful()
        assert validation.active is False
        assert validation.success is True
        assert validation.secret is None

        # A validation that has already succeeded cannot be marked
        # as successful.
        with pytest.raises(Exception) as exc:
            validation.mark_as_successful()
        assert "This validation has already succeeded" in str(exc.value)

        # A validation that has expired cannot be marked as successful.
        validation.restart()
        validation.started_at = (
            datetime.datetime.utcnow() - datetime.timedelta(days=7)
        )
        assert validation.active is False
        with pytest.raises(Exception) as exc:
            validation.mark_as_successful()
        assert "This validation has expired" in str(exc.value)


class TestAdmin(DatabaseTest):
    def setup(self):
        super(TestAdmin, self).setup()
        self.admin = self._admin()

    def test_make_password(self):
        assert self.admin.password.startswith("$2b$")

    def test_check_password(self):
        assert self.admin.check_password("123")
        assert not self.admin.check_password("wrong")

    def test_authenticate(self):
        # Successfully authenticate existing admin
        assert Admin.authenticate(self._db, "Admin", "123") == self.admin
        # Unsuccessfully authenticate existing admin
        assert Admin.authenticate(self._db, "Admin", "wrong") is None

    def test_make_new_admin(self):
        # Create the first admin
        self._db.delete(self.admin)
        new_admin = Admin.authenticate(self._db, "New", "password")
        assert new_admin.username == "New"
        assert new_admin.password.startswith("$2b$")
        # Now that there's an admin, subsequent attempts to make a new admin won't work.
        another_admin = Admin.authenticate(self._db, "Another", "password")
        assert another_admin is None
