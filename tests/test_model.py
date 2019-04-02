from nose.tools import (
    assert_raises_regexp,
    assert_raises,
    eq_,
    set_trace,
)
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import MultipleResultsFound
import base64
import datetime
import json
import operator
import random

from config import Configuration
from emailer import Emailer
from model import (
    create,
    get_one,
    get_one_or_create,
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
        eq_(True, is_new)

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
        eq_(new_york, lake_placid.parent)
        eq_([lake_placid], new_york.children)
        eq_([], new_mexico.children)

        # Query the database to find states ordered by distance from
        # Lake Placid.
        distance = func.ST_Distance_Sphere(
            lake_placid.geometry, Place.geometry
        )
        places = self._db.query(Place).filter(
            Place.type==Place.STATE).order_by(distance).add_columns(distance)

        # We can find the distance in kilometers between the 'Lake
        # Placid' point and the points representing the other states.
        eq_(
            [
                ("New York", 172),
                ("Connecticut", 285),
                ("New Mexico", 2993)
            ],
            [(x[0].external_name, int(x[1]/1000)) for x in places]
        )

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
        eq_([alias], new_york.aliases)

    def test_default_nation(self):
        m = Place.default_nation

        # We start out with no default nation.
        eq_(None, m(self._db))

        # The abbreviation of the default nation is stored in the
        # DEFAULT_NATION_ABBREVIATION setting.
        setting = ConfigurationSetting.sitewide(
            self._db, Configuration.DEFAULT_NATION_ABBREVIATION
        )
        eq_(None, setting.value)

        # Set the default nation to the United States.
        setting.value = self.crude_us.abbreviated_name
        eq_(self.crude_us, m(self._db))

        # If there's no nation with this abbreviation,
        # there is no default nation.
        setting.value = "LL"
        eq_(None, m(self._db))

    def test_to_geojson(self):

        # If you ask for the GeoJSON of one place, that place is
        # returned as-is.
        zip1 = self.zip_10018
        geojson = Place.to_geojson(self._db, zip1)
        eq_(geojson, json.loads(self.zip_10018_geojson))

        # If you ask for GeoJSON of several places, it's returned as a
        # GeometryCollection document.
        zip2 = self.zip_11212
        geojson = Place.to_geojson(self._db, zip1, zip2)
        eq_("GeometryCollection", geojson['type'])

        # There are two geometries in this document -- one for each
        # Place we passed in.
        geometries = geojson['geometries']
        eq_(2, len(geometries))
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
        eq_(True, s_i(nyc, new_york))
        eq_(True, s_i(new_york, nyc))

        # Places that don't share a border don't intersect.
        eq_(False, s_i(nyc, connecticut))
        eq_(False, s_i(connecticut, nyc))

        # Connecticut and New York share a border, so PostGIS says they
        # intersect, but they don't "intersect" in the everyday sense,
        # so overlaps_not_counting_border excludes them.
        eq_(False, s_i(new_york, connecticut))
        eq_(False, s_i(connecticut, new_york))

    def test_parse_name(self):
        m = Place.parse_name
        eq_(("Kern", Place.COUNTY), m("Kern County"))
        eq_(("New York", Place.STATE), m("New York State"))
        eq_(("Chicago, IL", None), m("Chicago, IL"))

    def test_name_parts(self):
        m = Place.name_parts
        eq_(["MA", "Boston"], m("Boston, MA"))
        eq_(["MA", "Boston"], m("Boston, MA,"))
        eq_(["USA", "Anytown"], m("Anytown, USA"))
        eq_(["US", "Ohio", "Lake County"], m("Lake County, Ohio, US"))

    def test_lookup_by_name(self):

        # There are two places in California called 'Santa Barbara': a
        # city, and a county (which includes the city).
        sb_city = self._place(external_name="Santa Barbara", type=Place.CITY)
        sb_county = self._place(
            external_name="Santa Barbara", type=Place.COUNTY
        )

        # If we look up "Santa Barbara" by name, we get the city.
        m = Place.lookup_by_name
        eq_([sb_city], m(self._db, "Santa Barbara").all())

        # To get Santa Barbara County, we have to refer to
        # "Santa Barbara County"
        eq_(
            [sb_county], m(self._db, "Santa Barbara County").all()
        )

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
            eq_(expect, parent.lookup_inside(name, using_overlap=True))
            eq_(expect, parent.lookup_inside(name, using_overlap=False))

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
        eq_(
            kings_county,
            everywhere.lookup_inside("Kings County, US", using_overlap=True)
        )

        # Neither of these is obviously better.
        eq_(None, us.lookup_inside("Manhattan"))
        assert_raises_regexp(
            MultipleResultsFound,
            "More than one place called Manhattan inside United States.",
            us.lookup_inside, "Manhattan", using_overlap=True
        )

        # Now the cases where using_overlap=False performs better.

        # "New York, US" is a little ambiguous, but they probably mean
        # the state.
        eq_(new_york, us.lookup_inside("New York"))
        assert_raises_regexp(
            MultipleResultsFound,
            "More than one place called New York inside United States.",
            us.lookup_inside, "New York", using_overlap=True
        )

        # "New York, New York" can only be parsed by parentage.
        eq_(nyc, us.lookup_inside("New York, New York"))
        assert_raises_regexp(
            MultipleResultsFound,
            "More than one place called New York inside United States.",
            us.lookup_inside, "New York, New York", using_overlap=True
        )

        # Using geographic overlap has another problem -- although the
        # name of the method is 'lookup_inside', we're actually
        # checking for _intersection_. Places that overlap are treated
        # as being inside *each other*.
        eq_(nyc, zip_10018.lookup_inside("New York", using_overlap=True))
        eq_(None, zip_10018.lookup_inside("New York", using_overlap=False))

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
        eq_(zip_12601, poughkeepsie_zips)

        # If we ask about a real place but there is no corresponding
        # postal code Place in the database, we get nothing.
        eq_(None, m("Woodstock"))

        # Similarly if we ask about a nonexistent place.
        eq_(None, m("ZXCVB"))

        # Calling this method on a Place that's not a state doesn't
        # make sense (because uszipcode only knows about cities within
        # states), and the result is always None.
        eq_(None, zip_12601.lookup_one_through_external_source("Poughkeepsie"))

        # lookup_one_through_external_source operates on the same
        # rules as lookup_inside -- the city you're looking up must be
        # geographically inside the Place whose method you're calling.
        eq_(None,
            connecticut.lookup_one_through_external_source("Poughkeepsie"))

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
        eq_([nypl], zip.served_by().all())

        eq_([nypl], nyc.served_by().all())
        eq_([ct_state], connecticut.served_by().all())

        # New York and Connecticut share a border, and the Connecticut
        # state library serves the entire state, including the
        # border. Internally, we use overlaps_not_counting_border() to avoid
        # concluding that the Connecticut state library serves New
        # York.
        eq_([nypl], new_york.served_by().all())


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
        eq_("ABCD", lib.short_name)
        try:
            lib.short_name = 'ab|cd'
            raise Error("Expected exception not raised.")
        except ValueError, e:
            eq_('Short name cannot contain the pipe character.',
                e.message)

    def test_for_short_name(self):
        eq_(None, Library.for_short_name(self._db, 'ABCD'))
        lib = self._library("A Library")
        lib.short_name = 'ABCD'
        eq_(lib, Library.for_short_name(self._db, 'ABCD'))

    def test_for_urn(self):
        eq_(None, Library.for_urn(self._db, 'ABCD'))
        lib = self._library()
        eq_(lib, Library.for_urn(self._db, lib.internal_urn))

    def test_random_short_name(self):
        # First, try with no duplicate check.
        random.seed(42)
        name = Library.random_short_name()
        eq_("QAHFTR", name)

        # Reset the random seed so the same name will be generated again.
        random.seed(42)
        # Create a duplicate_check implementation that claims QAHFTR
        # has already been used.
        def already_used(name):
            return name == "QAHFTR"
        name = Library.random_short_name(duplicate_check=already_used)

        # random_short_name now generates QAHFTR, but it's a
        # duplicate, so it tries again and generates a new string
        # which passes the already_used test.
        eq_("XCKAFN", name)

        # To avoid an infinite loop, we will stop trying and raise an
        # exception after a certain number of attempts (the default is
        # 20).
        def theyre_all_duplicates(name):
            return True
        assert_raises_regexp(
            ValueError,
            "Could not generate random short name after 20 attempts!",
            Library.random_short_name,
            duplicate_check=theyre_all_duplicates
        )

    def test_set_library_stage(self):
        lib = self._library()

        # We can't change library_stage because only the registry can
        # take a library from production to non-production.
        def crash():
            lib.library_stage = Library.TESTING_STAGE
        assert_raises_regexp(
            ValueError, "This library is already in production", crash
        )

        # Have the registry take the library out of production.
        lib.registry_stage = Library.CANCELLED_STAGE
        eq_(False, lib.in_production)

        # Now we can change the library stage however we want.
        lib.library_stage = Library.TESTING_STAGE
        lib.library_stage = Library.CANCELLED_STAGE
        lib.library_stage = Library.PRODUCTION_STAGE

    def test_in_production(self):
        lib = self._library()

        # The testing code creates a library that starts out in
        # production.
        eq_(Library.PRODUCTION_STAGE, lib.library_stage)
        eq_(Library.PRODUCTION_STAGE, lib.registry_stage)
        eq_(True, lib.in_production)

        # If either library_stage or registry stage is not
        # PRODUCTION_STAGE, we are not in production.
        lib.registry_stage = Library.CANCELLED_STAGE
        eq_(False, lib.in_production)

        lib.library_stage = Library.CANCELLED_STAGE
        eq_(False, lib.in_production)

        lib.registry_stage = Library.PRODUCTION_STAGE
        eq_(False, lib.in_production)

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
            eq_([library], feed(production))

        # Now one party thinks the library is in the testing stage.
        library.registry_stage = Library.TESTING_STAGE

        # It shows up in the testing feed but not the production feed.
        eq_([], feed(True))
        eq_([library], feed(False))

        library.library_stage = Library.TESTING_STAGE
        library.registry_stage = Library.PRODUCTION_STAGE
        eq_([], feed(True))
        eq_([library], feed(False))

        # Now on party thinks the library is in the cancelled stage,
        # and it will not show up in eithre feed.
        library.library_stage = Library.CANCELLED_STAGE
        for production in (True, False):
            eq_([], feed(production))

    def test_set_hyperlink(self):
        library = self._library()

        assert_raises_regexp(
            ValueError, "No Hyperlink hrefs were specified",
            library.set_hyperlink, "rel"
        )

        assert_raises_regexp(
            ValueError, "No link relation was specified",
            library.set_hyperlink, None, ["href"]
        )

        link, is_modified = library.set_hyperlink("rel", "href1", "href2")
        eq_("rel", link.rel)
        eq_("href1", link.href)
        eq_(True, is_modified)

        # Calling set_hyperlink again does not modify the link
        # so long as the old href is still a possibility.
        link2, is_modified = library.set_hyperlink("rel", "href2", "href1")
        eq_(link2, link)
        eq_("rel", link2.rel)
        eq_("href1", link2.href)
        eq_(False, is_modified)

        # If there is no way to keep a Hyperlink's href intact,
        # set_hyperlink will modify it.
        link3, is_modified = library.set_hyperlink("rel", "href2", "href3")
        eq_(link3, link)
        eq_("rel", link3.rel)
        eq_("href2", link3.href)
        eq_(True, is_modified)

        # Under no circumstances will two hyperlinks for the same rel be
        # created for a given library.
        eq_([link3], library.hyperlinks)

        # However, a library can have multiple hyperlinks to the same
        # Resource using different rels.
        link4, modified = library.set_hyperlink("rel2", "href2")
        eq_(link4.resource, link3.resource)
        eq_(True, modified)

        # And two libraries can link to the same Resource using the same
        # rel.
        library2 = self._library()
        link5, modified = library2.set_hyperlink("rel2", "href2")
        eq_(True, modified)
        eq_(library2, link5.library)
        eq_(link4.resource, link5.resource)

    def test_get_hyperlink(self):
        library = self._library()
        link1, is_modified = library.set_hyperlink("contact_email", "contact_href")
        link2, is_modified = library.set_hyperlink("help_email", "help_href")

        contact_link = Library.get_hyperlink(library, "contact_email")
        eq_(contact_link, link1)

        help_link = Library.get_hyperlink(library, "help_email")
        eq_(help_link, link2)

    def test_library_service_area(self):
        zip = self.zip_10018
        nypl = self._library("New York Public Library", eligibility_areas=[zip])
        [service_area] = nypl.service_areas
        eq_(zip, service_area.place)
        eq_(nypl, service_area.library)

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
        eq_(public, lib)

        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (40.65, -73.94), 'eng', audiences=[Audience.RESEARCH]).most_common()
        eq_(research, lib1)
        eq_(public, lib2)

        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (40.65, -73.94), 'eng', audiences=[Audience.EDUCATIONAL_PRIMARY]).most_common()
        eq_(education, lib1)
        eq_(public, lib2)

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
        eq_(large, lib1)
        eq_(small, lib2)
        eq_(unknown, lib3)
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
        eq_(nypl, lib1)
        eq_(ct_state, lib2)

        # From this point in Connecticut, CT State is the closest.
        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (41.3, -73.3), 'eng').most_common()
        eq_(ct_state, lib1)
        eq_(nypl, lib2)

        # From this point in New Jersey, NYPL is closest.
        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (40.72, -74.47), 'eng').most_common()
        eq_(nypl, lib1)
        eq_(ct_state, lib2)

        # From this point in the Indian Ocean, both libraries
        # are so far away they're below the score threshold.
        eq_([], list(Library.relevant(self._db, (-15, 91), 'eng').most_common()))

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
        eq_(nypl, lib1)
        eq_(ct_state, lib2)

        # From this point in Connecticut, CT State is the closest.
        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (41.3, -73.3), 'eng').most_common()
        eq_(ct_state, lib1)
        eq_(nypl, lib2)

        # From this point in New Jersey, NYPL is closest.
        [(lib1, s1), (lib2, s2)] = Library.relevant(self._db, (40.72, -74.47), 'eng').most_common()
        eq_(nypl, lib1)
        eq_(ct_state, lib2)

        # From this point in the Indian Ocean, both libraries
        # are so far away they're below the score threshold.
        eq_([], list(Library.relevant(self._db, (-15, 91), 'eng').most_common()))

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
        eq_(nypl, lib1)
        eq_(ny_state, lib2)

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
        eq_(nypl, lib)

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
        eq_(4, len(libraries))
        eq_([nypl, bpl, internet_archive, nyu_press],
            [l[0] for l in libraries])

        # In Brooklyn.
        libraries = Library.relevant(self._db, (40.65, -73.94), "eng").most_common()
        eq_(4, len(libraries))
        eq_([bpl, nypl, internet_archive, nyu_press],
            [l[0] for l in libraries])

        # In Queens.
        libraries = Library.relevant(self._db, (40.76, -73.91), "eng").most_common()
        eq_(4, len(libraries))
        eq_([nypl, bpl, internet_archive, nyu_press],
            [l[0] for l in libraries])

        # In Albany.
        libraries = Library.relevant(self._db, (42.66, -73.77), "eng").most_common()
        eq_(5, len(libraries))
        eq_([albany, nypl, bpl, internet_archive, nyu_press],
            [l[0] for l in libraries])

        # In Syracuse (200km west of Albany).
        libraries = Library.relevant(self._db, (43.06, -76.15), "eng").most_common()
        eq_(4, len(libraries))
        eq_([nypl, bpl, internet_archive, nyu_press],
            [l[0] for l in libraries])

        # In New Jersey.
        libraries = Library.relevant(self._db, (40.79, -74.43), "eng").most_common()
        eq_(4, len(libraries))
        eq_([nypl, bpl, internet_archive, nyu_press],
            [l[0] for l in libraries])

        # In Las Cruces, NM. Internet Archive is first at the moment
        # due to its large collection, but maybe it would be better if UNM was.
        libraries = Library.relevant(self._db, (32.32, -106.77), "eng").most_common()
        eq_(2, len(libraries))
        eq_(set([unm, internet_archive]),
            set([l[0] for l in libraries]))

        # Russian speaker in Albany. Albany doesn't pass the score threshold
        # since it didn't report having any Russian books, but maybe we should
        # consider the total collection size as well as the user's language.
        libraries = Library.relevant(self._db, (42.66, -73.77), "rus").most_common()
        eq_(2, len(libraries))
        eq_([nypl, internet_archive],
            [l[0] for l in libraries])

        # Spanish speaker in Manhattan.
        libraries = Library.relevant(self._db, (40.75, -73.98), "spa").most_common()
        eq_(4, len(libraries))
        eq_([nypl, bpl, internet_archive, unm],
            [l[0] for l in libraries])

        # Patron with a print disability in Manhattan.
        libraries = Library.relevant(self._db, (40.75, -73.98), "eng", audiences=[Audience.PRINT_DISABILITY]).most_common()
        eq_(5, len(libraries))
        eq_([bard, nypl, bpl, internet_archive, nyu_press],
            [l[0] for l in libraries])

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

        eq_(0, d1)
        eq_(nypl, lib1)

        eq_(44, int(d2/1000))
        eq_(ct_state, lib2)

        # From this point in Connecticut, CT State is the closest
        # library (0 km away), so it shows up first, but NYPL (61 km
        # away) also shows up as a possibility.
        [(lib1, d1), (lib2, d2)] = Library.nearby(self._db, (41.3, -73.3))
        eq_(ct_state, lib1)
        eq_(0, d1)

        eq_(nypl, lib2)
        eq_(61, int(d2/1000))

        # From this point in Pennsylvania, NYPL shows up (142km away) but
        # CT State does not.
        [(lib1, d1)] = Library.nearby(self._db, (40, -75.8))
        eq_(nypl, lib1)
        eq_(142, int(d1/1000))

        # If we only look within a 100km radius, then there are no
        # libraries near that point in Pennsylvania.
        eq_([], Library.nearby(self._db, (40, -75.8), 100).all())

        # By default, nearby() only finds libraries that are in production.
        def m(production):
            return Library.nearby(
                self._db, (41.3, -73.3), production=production
            ).count()
        # Take all the libraries we found earlier out of production.
        for l in ct_state, nypl:
            l.registry_stage = Library.TESTING_STAGE
        # Now there are no results.
        eq_(0, m(True))

        # But we can run a search that includes libraries in the TESTING stage.
        eq_(2, m(False))

    def test_query_cleanup(self):
        m = Library.query_cleanup

        eq_("the library", m("THE LIBRARY"))
        eq_("the library", m("\tthe   library\n\n"))
        eq_("the library", m("the libary"))

    def test_as_postal_code(self):
        m = Library.as_postal_code
        # US ZIP codes are recognized as postal codes.
        eq_("93203", m("93203"))
        eq_("93203", m("93203-1234"))
        eq_(None, m("the library"))

        # A UK post code is not currently recognized.
        eq_(None, m("AB1 0AA"))

    def test_query_parts(self):
        m = Library.query_parts
        eq_((None, "93203", Place.POSTAL_CODE), m("93203"))
        eq_(("new york public library", "new york", None),
            m("new york public library"))
        eq_(("queens library", "queens", None), m("queens library"))
        eq_(("kern county library", "kern", Place.COUNTY),
            m("kern county library"))
        eq_(("new york state library", "new york", Place.STATE),
            m("new york state library"))
        eq_(("lapl", "lapl", None), m("lapl"))

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
        eq_([brooklyn], search("brooklyn public library"))

        # We can tolerate a small number of typos in a name or alias
        # that is longer than 6 characters.
        eq_([brooklyn], search("broklyn public library"))
        get_one_or_create(
            self._db, LibraryAlias, name="Bklynlib", language=None,
            library=brooklyn
        )
        eq_([brooklyn], search("zklynlib"))

        # The Boston Public Library serves Boston, MA.
        boston = self._library(
            name="Boston Public Library", focus_areas=[self.boston_ma]
        )

        # Both libraries are known colloquially as 'BPL'.
        for library in (brooklyn, boston):
            get_one_or_create(
                self._db, LibraryAlias, name="BPL", language=None,
                library=library
            )
        eq_(
            set([brooklyn, boston]), set(search("bpl"))
        )

        # We do not tolerate typos in short names, because the chance of
        # ambiguity is so high.
        eq_([], search("opl"))

        # If we're searching for "BPL" from California, Brooklyn shows
        # up first, because it's closer to California.
        eq_(["Brooklyn Public Library",
             "Boston Public Library"],
            [x[0].name for x in search("bpl", GeometryUtility.point(35, -118))])

        # If we're searching for "BPL" from Maine, Boston shows
        # up first, because it's closer to Maine.
        eq_(["Boston Public Library",
             "Brooklyn Public Library"],
            [x[0].name for x in search("bpl", GeometryUtility.point(43, -70))]
        )

        # By default, search_by_library_name() only finds libraries
        # in production. Put them in the TESTING stage and they disappear.
        for l in (brooklyn, boston):
            l.registry_stage = Library.TESTING_STAGE
        eq_([], search("bpl", production=True))

        # But you can find them by passing in production=False.
        eq_(2, len(search("bpl", production=False)))

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
        eq_("Kansas", kansas.external_name)
        eq_(Place.STATE, kansas.type)
        manhattan_ks = self.manhattan_ks

        # A search for 'manhattan' finds both libraries.
        libraries = list(Library.search_by_location_name(self._db, "manhattan"))
        eq_(set(["NYPL", "Kansas State Library"]),
            set([x.name for x in libraries])
        )

        # If you're searching from California, the Kansas library
        # shows up first.
        ca_results = Library.search_by_location_name(
            self._db, "manhattan", here=GeometryUtility.point(35, -118)
        )
        eq_(["Kansas State Library", "NYPL"], [x[0].name for x in ca_results])

        # If you're searching from Maine, the New York library shows
        # up first.
        me_results = Library.search_by_location_name(
            self._db, "manhattan", here=GeometryUtility.point(43, -70)
        )
        eq_(["NYPL", "Kansas State Library"], [x[0].name for x in me_results])

        # We can insist that only certain types of places be considered as
        # matching the name. There is no state called 'Manhattan', so
        # this query finds nothing.
        excluded = Library.search_by_location_name(
            self._db, "manhattan", type=Place.STATE
        )
        eq_([], excluded.all())

        # A search for "Brooklyn" finds the NYPL, but it only finds it
        # once, even though NYPL is associated with two places called
        # "Brooklyn": New York City and the ZIP code 11212
        [brooklyn_results] = Library.search_by_location_name(
            self._db, "brooklyn", here=GeometryUtility.point(43, -70)
        )
        eq_(nypl, brooklyn_results[0])

        nypl.registry_stage = Library.TESTING_STAGE
        eq_([],
            Library.search_by_location_name(
                self._db, "brooklyn", here=GeometryUtility.point(43, -70),
                production=True
            ).all()
        )

        eq_(1,
            Library.search_by_location_name(
                self._db, "brooklyn", here=GeometryUtility.point(43, -70),
                production=False
            ).count()
        )

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
        eq_(['Now Work', 'NYPL'], [x[0].name for x in libraries])
        eq_([1768, 0], [int(x[1]/1000) for x in libraries])

        # This search query has a Levenshtein distance of 1 from "New
        # York", but a distance of 3 from "Now Work", so only NYPL
        # shows up.
        #
        # Although "NEW YORM" matches both the city and state, both of
        # which intersect with NYPL's service area, NYPL only shows up
        # once.
        libraries = Library.search(self._db, (40.7, -73.9), "NEW YORM")
        eq_(['NYPL'], [x[0].name for x in libraries])

        # Searching for a place name picks up libraries whose service
        # areas intersect with that place.
        libraries = Library.search(self._db, (40.7, -73.9), "Kansas")
        eq_(['Now Work'], [x[0].name for x in libraries])

        # By default, search() only finds libraries in production.
        self.nypl.registry_stage = Library.TESTING_STAGE
        new_work.registry_stage = Library.TESTING_STAGE
        def m(production):
            return len(
                Library.search(
                    self._db, (40.7, -73.9), "New York", production
                )
            )
        eq_(0, m(True))

        # But you can find libraries that are in the testing stage
        # by passing in production=False.
        eq_(2, m(False))

    def test_search_excludes_duplicates(self):
        # Here's a library that serves a place called Kansas
        # whose name is also "Kansas"
        library = self._library(name="Kansas", focus_areas=[self.kansas_state])
        # It matches both the name search and the location search.
        eq_([library],
            Library.search_by_location_name(self._db, "kansas").all())
        eq_([library],
            Library.search_by_library_name(self._db, "kansas").all())

        # But when we do the general search, the library only shows up once.
        [(result, distance)] = Library.search(self._db, (0, 0), "Kansas")
        eq_(library, result)


class TestCollectionSummary(DatabaseTest):

    def test_set(self):
        library = self._library()
        summary = CollectionSummary.set(library, "eng", 100)
        eq_(library, summary.library)
        eq_("eng", summary.language)
        eq_(100, summary.size)

        # Call set() again and we get the same object back.
        summary2 = CollectionSummary.set(library, "eng", "0")
        eq_(summary, summary2)
        eq_(0, summary.size)

    def test_unrecognized_language_is_set_as_unknown(self):
        library = self._library()
        summary = CollectionSummary.set(library, "mmmmmm", 100)
        eq_(None, summary.language)
        eq_(100, summary.size)

    def test_size_must_be_integerable(self):
        library  = self._library()
        assert_raises_regexp(
            ValueError,
            "invalid literal for.*",
            CollectionSummary.set, library, "eng",
            "fruit"
        )

    def test_negative_size_is_not_allowed(self):
        library  = self._library()
        assert_raises_regexp(
            ValueError, "Collection size cannot be negative.",
            CollectionSummary.set, library, "eng", "-1"
        )

class TestAudience(DatabaseTest):
    def test_unrecognized_audience(self):
        assert_raises_regexp(
            ValueError,
            "Unknown audience: no such audience",
            Audience.lookup,
            self._db,
            "no such audience"
        )


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
        eq_(True, is_new)
        eq_(library, identifier.library)
        eq_(patron_identifier, identifier.patron_identifier)
        # id_1() was called.
        eq_("id1", identifier.delegated_identifier)

        # Try the same thing again but provide a different create_function
        # that raises an exception if called.
        def explode():
            raise Exception("I should never be called.")
        identifier2, is_new = DelegatedPatronIdentifier.get_one_or_create(
            self._db, library, patron_identifier, identifier_type, explode
        )
        # The existing identifier was looked up.
        eq_(False, is_new)
        eq_(identifier2.id, identifier.id)
        # id_2() was not called.
        eq_("id1", identifier2.delegated_identifier)

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
        eq_([], self.external_integration.settings)

        setting = self.external_integration.set_setting("website_id", "id1")
        eq_("website_id", setting.key)
        eq_("id1", setting.value)

        # Calling set() again updates the key-value pair.
        eq_([setting], self.external_integration.settings)
        setting2 = self.external_integration.set_setting("website_id", "id2")
        eq_(setting, setting2)
        eq_("id2", setting2.value)

        eq_(setting2, self.external_integration.setting("website_id"))

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
        eq_(expect, "\n".join(actual))

        # If we pass in True for include_secrets, we see the passwords.
        with_secrets = integration.explain(include_secrets=True)
        assert "password='somepass'" in with_secrets

class TestConfigurationSetting(DatabaseTest):

    def test_is_secret(self):
        """Some configuration settings are considered secrets,
        and some are not.
        """
        m = ConfigurationSetting._is_secret
        eq_(True, m('secret'))
        eq_(True, m('password'))
        eq_(True, m('its_a_secret_to_everybody'))
        eq_(True, m('the_password'))
        eq_(True, m('password_for_the_account'))
        eq_(False, m('public_information'))

        eq_(True,
            ConfigurationSetting.sitewide(self._db, "secret_key").is_secret)
        eq_(False,
            ConfigurationSetting.sitewide(self._db, "public_key").is_secret)

    def test_value_or_default(self):
        integration, ignore = create(
            self._db, ExternalIntegration, goal=self._str, protocol=self._str
        )
        setting = integration.setting("key")
        eq_(None, setting.value)

        # If the setting has no value, value_or_default sets the value to
        # the default, and returns the default.
        eq_("default value", setting.value_or_default("default value"))
        eq_("default value", setting.value)

        # Once the value is set, value_or_default returns the value.
        eq_("default value", setting.value_or_default("new default"))

        # If the setting has any value at all, even the empty string,
        # it's returned instead of the default.
        setting.value = ""
        eq_("", setting.value_or_default("default"))

    def test_value_inheritance(self):

        key = "SomeKey"

        # Here's a sitewide configuration setting.
        sitewide_conf = ConfigurationSetting.sitewide(self._db, key)

        # Its value is not set.
        eq_(None, sitewide_conf.value)

        # Set it.
        sitewide_conf.value = "Sitewide value"
        eq_("Sitewide value", sitewide_conf.value)

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
        eq_(None, adobe_conf.value)
        adobe_conf.value = "Adobe value"

        # Here's a library which has a ConfigurationSetting for the same
        # key used in the sitewide configuration.
        library = self._library()
        library_conf = ConfigurationSetting.for_library(key, library)

        # Since all libraries use a given ConfigurationSetting to mean
        # the same thing, a library _does_ inherit the sitewide value
        # for a configuration setting.
        eq_("Sitewide value", library_conf.value)

        # Change the site-wide configuration, and the default also changes.
        sitewide_conf.value = "New site-wide value"
        eq_("New site-wide value", library_conf.value)

        # The per-library value takes precedence over the site-wide
        # value.
        library_conf.value = "Per-library value"
        eq_("Per-library value", library_conf.value)

        # Now let's consider a setting like on the combination of a library and an
        # integration integration.
        key = "patron_identifier_prefix"
        library_patron_prefix_conf = ConfigurationSetting.for_library_and_externalintegration(
            self._db, key, library, adobe
        )
        eq_(None, library_patron_prefix_conf.value)

        # If the integration has a value set for this
        # ConfigurationSetting, that value is inherited for every
        # individual library that uses the integration.
        generic_patron_prefix_conf = ConfigurationSetting.for_externalintegration(
            key, adobe
        )
        eq_(None, generic_patron_prefix_conf.value)
        generic_patron_prefix_conf.value = "Integration-specific value"
        eq_("Integration-specific value", library_patron_prefix_conf.value)

        # Change the value on the integration, and the default changes
        # for each individual library.
        generic_patron_prefix_conf.value = "New integration-specific value"
        eq_("New integration-specific value", library_patron_prefix_conf.value)

        # The library+integration setting takes precedence over the
        # integration setting.
        library_patron_prefix_conf.value = "Library-specific value"
        eq_("Library-specific value", library_patron_prefix_conf.value)

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
        eq_(setting, setting2)
        assert_raises(
            IntegrityError,
            create, self._db, ConfigurationSetting,
            key=key,
            library_id=library.id, external_integration=integration
        )
        # We really screwed up the database session there -- roll it back
        # so that test cleanup can proceed.
        self._db.rollback()

    def test_int_value(self):
        number = ConfigurationSetting.sitewide(self._db, "number")
        eq_(None, number.int_value)

        number.value = "1234"
        eq_(1234, number.int_value)

        number.value = "tra la la"
        assert_raises(ValueError, lambda: number.int_value)

    def test_float_value(self):
        number = ConfigurationSetting.sitewide(self._db, "number")
        eq_(None, number.int_value)

        number.value = "1234.5"
        eq_(1234.5, number.float_value)

        number.value = "tra la la"
        assert_raises(ValueError, lambda: number.float_value)

    def test_json_value(self):
        jsondata = ConfigurationSetting.sitewide(self._db, "json")
        eq_(None, jsondata.int_value)

        jsondata.value = "[1,2]"
        eq_([1,2], jsondata.json_value)

        jsondata.value = "tra la la"
        assert_raises(ValueError, lambda: jsondata.json_value)

    def test_explain(self):
        """Test that ConfigurationSetting.explain gives information
        about all site-wide configuration settings.
        """
        ConfigurationSetting.sitewide(self._db, "a_secret").value = "1"
        ConfigurationSetting.sitewide(self._db, "nonsecret_setting").value = "2"

        integration, ignore = create(
            self._db, ExternalIntegration,
            protocol="a protocol", goal="a goal")

        actual = ConfigurationSetting.explain(self._db, include_secrets=True)
        expect = """Site-wide configuration settings:
---------------------------------
a_secret='1'
nonsecret_setting='2'"""
        eq_(expect, "\n".join(actual))

        without_secrets = "\n".join(ConfigurationSetting.explain(
            self._db, include_secrets=False
        ))
        assert 'a_secret' not in without_secrets
        assert 'nonsecret_setting' in without_secrets


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
        eq_(emailer.ADDRESS_NEEDS_CONFIRMATION, type)
        eq_("you@library", sent_to)

        # These arguments were created to fill in the ADDRESS_NEEDS_CONFIRMATION
        # template.
        eq_("me@registry", kwargs['registry_support'])
        eq_("you@library", kwargs['email'])
        eq_("copyright designated agent", kwargs['rel_desc'])
        eq_(library.name, kwargs['library'])
        eq_(library.web_url, kwargs['library_web_url'])
        eq_("http://url/", kwargs['confirmation_link'])

        # url_for was called to create the confirmation link.
        controller, kwargs = emailer.url_for_calls.pop()
        eq_("confirm_resource", controller)
        eq_(secret, kwargs['secret'])
        eq_(link.resource.id, kwargs['resource_id'])

        # If a Resource we already know about is associated with
        # a new Hyperlink, an ADDRESS_DESIGNATED email is sent instead.
        link2, is_modified = library.set_hyperlink("help", "mailto:you@library")
        link2.notify(emailer, emailer.url_for)

        (type, href, kwargs) = emailer.sent.pop()
        eq_(emailer.ADDRESS_DESIGNATED, type)
        eq_("patron help contact address", kwargs['rel_desc'])

        # url_for was not called again, since an ADDRESS_DESIGNATED
        # email does not include a validation link.
        eq_([], emailer.url_for_calls)

        # And the Validation was not reset.
        eq_(secret, link.resource.validation.secret)

        # Same if we somehow send another notification for a Hyperlink with an
        # active Validation.
        link.notify(emailer, emailer.url_for)
        (type, href, kwargs) = emailer.sent.pop()
        eq_(emailer.ADDRESS_DESIGNATED, type)
        eq_(secret, link.resource.validation.secret)

        # However, if a Hyperlink's Validation has expired, it's reset and a new
        # ADDRESS_NEEDS_CONFIRMATION email is sent out.
        now = datetime.datetime.utcnow()
        link.resource.validation.started_at = (now - datetime.timedelta(days=10))
        link.notify(emailer, emailer.url_for)
        (type, href, kwargs) = emailer.sent.pop()
        eq_(emailer.ADDRESS_NEEDS_CONFIRMATION, type)
        assert 'confirmation_link' in kwargs

        # The Validation has been reset.
        eq_(validation, link.resource.validation)
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
        eq_(email_validation, email_validation_2)
        eq_(False, email_validation_2.success)

        # The secret has changed.
        assert old_secret != email_validation.secret

    def test_mark_as_successful(self):

        validation, ignore = create(self._db, Validation)
        eq_(True, validation.active)
        eq_(False, validation.success)
        assert validation.secret is not None

        validation.mark_as_successful()
        eq_(False, validation.active)
        eq_(True, validation.success)
        eq_(None, validation.secret)

        # A validation that has already succeeded cannot be marked
        # as successful.
        assert_raises_regexp(
            Exception, "This validation has already succeeded",
            validation.mark_as_successful
        )

        # A validation that has expired cannot be marked as successful.
        validation.restart()
        validation.started_at = (
            datetime.datetime.utcnow() - datetime.timedelta(days=7)
        )
        eq_(False, validation.active)
        assert_raises_regexp(
            Exception, "This validation has expired",
            validation.mark_as_successful
        )
