import random

import pytest

from palace.registry.sqlalchemy.constants import LibraryType
from palace.registry.sqlalchemy.model.audience import Audience
from palace.registry.sqlalchemy.model.collection_summary import CollectionSummary
from palace.registry.sqlalchemy.model.delegated_patron_identifier import (
    DelegatedPatronIdentifier,
)
from palace.registry.sqlalchemy.model.library import Library, LibraryAlias
from palace.registry.sqlalchemy.model.place import Place
from palace.registry.sqlalchemy.util import get_one_or_create
from palace.registry.util import GeometryUtility
from palace.registry.util.datetime_helpers import utc_now
from tests.fixtures.database import DatabaseTransactionFixture


class TestLibrary:
    def test_timestamp(self, db: DatabaseTransactionFixture):
        """Timestamp gets automatically set on database commit."""
        nypl = db.library("New York Public Library")
        first_modified = nypl.timestamp
        now = utc_now()
        db.session.commit()
        assert (now - first_modified).seconds < 2

        nypl.opds_url = "http://library/"
        db.session.commit()
        assert nypl.timestamp > first_modified

    def test_short_name(self, db: DatabaseTransactionFixture):
        lib = db.library("A Library")
        lib.short_name = "abcd"
        assert lib.short_name == "ABCD"
        with pytest.raises(ValueError) as e:
            lib.short_name = "ab|cd"
        assert "Short name cannot contain the pipe character." in str(e)

    def test_for_short_name(self, db: DatabaseTransactionFixture):
        assert Library.for_short_name(db.session, "ABCD") is None
        lib = db.library("A Library")
        lib.short_name = "ABCD"
        assert Library.for_short_name(db.session, "ABCD") == lib

    def test_for_urn(self, db: DatabaseTransactionFixture):
        assert Library.for_urn(db.session, "ABCD") is None
        lib = db.library()
        assert Library.for_urn(db.session, lib.internal_urn) == lib

    def test_random_short_name(self):
        # First, try with no duplicate check.
        random.seed(42)
        name = Library.random_short_name()

        expect = "UDAXIH"
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
        assert "Could not generate random short name after 20 attempts!" in str(
            exc.value
        )

    def test_set_library_stage(self, db: DatabaseTransactionFixture):
        lib = db.library()

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

    def test_in_production(self, db: DatabaseTransactionFixture):
        lib = db.library()

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

    def test_number_of_patrons(self, db: DatabaseTransactionFixture):
        production_library = db.library()
        assert production_library.number_of_patrons == 0
        identifier1, is_new = DelegatedPatronIdentifier.get_one_or_create(
            db.session,
            production_library,
            db.fresh_str(),
            DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID,
            None,
        )
        assert production_library.number_of_patrons == 1

        # Identifiers for another library don't count towards the total.
        production_library_2 = db.library()
        identifier1, is_new = DelegatedPatronIdentifier.get_one_or_create(
            db.session,
            production_library_2,
            db.fresh_str(),
            DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID,
            None,
        )
        assert production_library.number_of_patrons == 1

        # Identifiers that aren't Adobe Account IDs don't count towards the total.
        identifier2, is_new = DelegatedPatronIdentifier.get_one_or_create(
            db.session, production_library, db.fresh_str(), "abc", None
        )
        assert production_library.number_of_patrons == 1
        # Identifiers can't be assigned to libraries that aren't in production.
        testing_library = db.library(library_stage=Library.TESTING_STAGE)
        assert testing_library.number_of_patrons == 0
        identifier3, is_new = DelegatedPatronIdentifier.get_one_or_create(
            db.session,
            testing_library,
            db.fresh_str(),
            DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID,
            None,
        )
        assert testing_library.number_of_patrons == 0

        # Using patron_counts_by_library you can determine patron counts for a number
        # of libraries at once.
        counts = Library.patron_counts_by_library(
            db.session, [production_library, production_library_2, testing_library]
        )
        assert counts == {
            production_library.id: 1,
            production_library_2.id: 1,
        }

    def test__feed_restriction(self, db: DatabaseTransactionFixture):
        """Test the _feed_restriction helper method."""

        def feed(production=True):
            """Find only libraries that belong in a certain feed."""
            qu = db.session.query(Library)
            qu = qu.filter(Library._feed_restriction(production))
            return qu.all()

        # This library starts out in production.
        library = db.library()

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

    def test_set_hyperlink(self, db: DatabaseTransactionFixture):
        library = db.library()

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
        library2 = db.library()
        link5, modified = library2.set_hyperlink("rel2", "href2")
        assert modified is True
        assert link5.library == library2
        assert link5.resource == link4.resource

    def test_get_hyperlink(self, db: DatabaseTransactionFixture):
        library = db.library()
        link1, is_modified = library.set_hyperlink("contact_email", "contact_href")
        link2, is_modified = library.set_hyperlink("help_email", "help_href")

        contact_link = Library.get_hyperlink(library, "contact_email")
        assert link1 == contact_link

        help_link = Library.get_hyperlink(library, "help_email")
        assert help_link == link2

    def test_library_service_area(self, db: DatabaseTransactionFixture):
        zip = db.zip_10018

        nypl = db.library("New York Public Library", eligibility_areas=[zip])
        [service_area] = nypl.service_areas
        assert service_area.place == zip
        assert service_area.library == nypl

    def test_types(self, db: DatabaseTransactionFixture):
        # Test the various types of libraries.
        # n.b. this incidentally tests Place.library_type.

        postal = db.zip_10018
        city = db.new_york_city
        state = db.new_york_state
        nation = db.place("CA", "Canada", Place.NATION, "CA", None)
        province = db.place("MB", "Manitoba", Place.STATE, "MB", nation)
        everywhere = Place.everywhere(db.session)

        # Libraries with different kinds of service areas are given
        # different types.
        for focus, type in (
            (postal, LibraryType.LOCAL),
            (city, LibraryType.LOCAL),
            (state, LibraryType.STATE),
            (province, LibraryType.PROVINCE),
            (nation, LibraryType.NATIONAL),
            (everywhere, LibraryType.UNIVERSAL),
        ):

            library = db.library(db.fresh_str(), focus_areas=[focus])
            assert focus.library_type == type
            assert [type] == list(library.types)

        # If a library's service area is ambiguous, it has no service
        # area-related type.
        library = db.library("library", focus_areas=[postal, province])
        assert [] == list(library.types)

    def test_service_area_name(self, db: DatabaseTransactionFixture):

        # Gather a few focus areas; the details don't matter.
        zip = db.zip_10018
        nyc = db.new_york_city
        new_york = db.new_york_state

        # 'Everywhere' is not a place with a distinctive name, so throughout
        # this test it will be ignored.
        everywhere = Place.everywhere(db.session)

        library = db.library(
            "Internet Archive", eligibility_areas=[everywhere], focus_areas=[everywhere]
        )
        assert None == library.service_area_name

        # A library with a single eligibility area has a
        # straightforward name.
        library = db.library(
            "test library",
            eligibility_areas=[everywhere, new_york],
            focus_areas=[everywhere],
        )
        assert "New York" == library.service_area_name

        # If you somehow specify the same place twice, it's fine.
        library = db.library(
            "test library",
            eligibility_areas=[new_york, new_york],
            focus_areas=[everywhere],
        )
        assert "New York" == library.service_area_name

        # If the library has an eligibility area and a focus area,
        # the focus area takes precedence.
        library = db.library(
            "test library",
            eligibility_areas=[everywhere, new_york],
            focus_areas=[nyc, everywhere],
        )
        assert "New York, NY" == library.service_area_name

        # If there are multiple focus areas and one eligibility area,
        # we're back to using the focus area.
        library = db.library(
            "test library",
            eligibility_areas=[everywhere, new_york],
            focus_areas=[nyc, zip, everywhere],
        )
        assert "New York" == library.service_area_name

        # If there are multiple focus areas _and_ multiple eligibility areas,
        # there's no one string that describes the service area.
        library = db.library(
            "test library",
            eligibility_areas=[everywhere, new_york, zip],
            focus_areas=[nyc, zip, everywhere],
        )
        assert None == library.service_area_name

    def test_relevant_audience(self, db: DatabaseTransactionFixture):
        research = db.library(
            "NYU Library",
            eligibility_areas=[db.new_york_city],
            focus_areas=[db.new_york_city],
            audiences=[Audience.RESEARCH],
        )
        public = db.library(
            "New York Public Library",
            eligibility_areas=[db.new_york_city],
            focus_areas=[db.new_york_city],
            audiences=[Audience.PUBLIC],
        )
        education = db.library(
            "School",
            eligibility_areas=[db.new_york_city],
            focus_areas=[db.new_york_city],
            audiences=[Audience.EDUCATIONAL_PRIMARY, Audience.EDUCATIONAL_SECONDARY],
        )
        db.session.flush()

        [(lib, s)] = Library.relevant(
            db.session, (40.65, -73.94), "eng", audiences=[Audience.PUBLIC]
        ).most_common()
        assert lib == public

        [(lib1, s1), (lib2, s2)] = Library.relevant(
            db.session, (40.65, -73.94), "eng", audiences=[Audience.RESEARCH]
        ).most_common()
        assert lib1 == research
        assert lib2 == public

        [(lib1, s1), (lib2, s2)] = Library.relevant(
            db.session, (40.65, -73.94), "eng", audiences=[Audience.EDUCATIONAL_PRIMARY]
        ).most_common()
        assert lib1 == education
        assert lib2 == public

    def test_relevant_collection_size(self, db: DatabaseTransactionFixture):
        small = db.library(
            "Small Library",
            eligibility_areas=[db.new_york_city],
            focus_areas=[db.new_york_city],
        )
        CollectionSummary.set(small, "eng", 10)
        large = db.library(
            "Large Library",
            eligibility_areas=[db.new_york_city],
            focus_areas=[db.new_york_city],
        )
        CollectionSummary.set(large, "eng", 100000)
        empty = db.library(
            "Empty Library",
            eligibility_areas=[db.new_york_city],
            focus_areas=[db.new_york_city],
        )
        CollectionSummary.set(empty, "eng", 0)
        unknown = db.library(
            "Unknown Library",
            eligibility_areas=[db.new_york_city],
            focus_areas=[db.new_york_city],
        )
        db.session.flush()

        [(lib1, s1), (lib2, s2), (lib3, s3)] = Library.relevant(
            db.session, (40.65, -73.94), "eng"
        ).most_common()
        assert lib1 == large
        assert lib2 == small
        assert lib3 == unknown
        # Empty isn't included because we're sure it has no books in English.

    def test_relevant_eligibility_area(self, db: DatabaseTransactionFixture):
        # Create two libraries. One serves New York City, and one serves
        # the entire state of Connecticut. They have the same focus area
        # so this only tests eligibility area.
        nypl = db.library(
            "New York Public Library",
            eligibility_areas=[db.new_york_city],
            focus_areas=[db.new_york_city, db.connecticut_state],
        )
        ct_state = db.library(
            "Connecticut State Library",
            eligibility_areas=[db.connecticut_state],
            focus_areas=[db.new_york_city, db.connecticut_state],
        )
        db.session.flush()

        # From this point in Brooklyn, NYPL is the closest library.
        [(lib1, s1), (lib2, s2)] = Library.relevant(
            db.session, (40.65, -73.94), "eng"
        ).most_common()
        assert lib1 == nypl
        assert lib2 == ct_state

        # From this point in Connecticut, CT State is the closest.
        [(lib1, s1), (lib2, s2)] = Library.relevant(
            db.session, (41.3, -73.3), "eng"
        ).most_common()
        assert lib1 == ct_state
        assert lib2 == nypl

        # From this point in New Jersey, NYPL is closest.
        [(lib1, s1), (lib2, s2)] = Library.relevant(
            db.session, (40.72, -74.47), "eng"
        ).most_common()
        assert lib1 == nypl
        assert lib2 == ct_state

        # From this point in the Indian Ocean, both libraries
        # are so far away they're below the score threshold.
        assert list(Library.relevant(db.session, (-15, 91), "eng").most_common()) == []

    def test_relevant_focus_area(self, db: DatabaseTransactionFixture):
        # Create two libraries. One serves New York City, and one serves
        # the entire state of Connecticut. They have the same eligibility
        # area, so this only tests focus area.
        nypl = db.library(
            "New York Public Library",
            focus_areas=[db.new_york_city],
            eligibility_areas=[db.new_york_city, db.connecticut_state],
        )
        ct_state = db.library(
            "Connecticut State Library",
            focus_areas=[db.connecticut_state],
            eligibility_areas=[db.new_york_city, db.connecticut_state],
        )
        db.session.flush()

        # From this point in Brooklyn, NYPL is the closest library.
        [(lib1, s1), (lib2, s2)] = Library.relevant(
            db.session, (40.65, -73.94), "eng"
        ).most_common()
        assert lib1 == nypl
        assert lib2 == ct_state

        # From this point in Connecticut, CT State is the closest.
        [(lib1, s1), (lib2, s2)] = Library.relevant(
            db.session, (41.3, -73.3), "eng"
        ).most_common()
        assert lib1 == ct_state
        assert lib2 == nypl

        # From this point in New Jersey, NYPL is closest.
        [(lib1, s1), (lib2, s2)] = Library.relevant(
            db.session, (40.72, -74.47), "eng"
        ).most_common()
        assert lib1 == nypl
        assert lib2 == ct_state

        # From this point in the Indian Ocean, both libraries
        # are so far away they're below the score threshold.
        assert list(Library.relevant(db.session, (-15, 91), "eng").most_common()) == []

    def test_relevant_focus_area_size(self, db: DatabaseTransactionFixture):
        # This library serves NYC.
        nypl = db.library(
            "New York Public Library",
            focus_areas=[db.new_york_city],
            eligibility_areas=[db.new_york_state],
        )
        # This library serves New York state.
        ny_state = db.library(
            "New York State Library",
            focus_areas=[db.new_york_state],
            eligibility_areas=[db.new_york_state],
        )
        db.session.flush()

        # This point in Brooklyn is in both libraries' focus areas,
        # but NYPL has a smaller focus area so it wins.
        [(lib1, s1), (lib2, s2)] = Library.relevant(
            db.session, (40.65, -73.94), "eng"
        ).most_common()
        assert lib1 == nypl
        assert lib2 == ny_state

    def test_relevant_library_with_no_service_areas(
        self, db: DatabaseTransactionFixture
    ):
        # Make sure a library with no service areas doesn't crash the query.

        # This library serves NYC.
        nypl = db.library(
            "New York Public Library",
            focus_areas=[db.new_york_city],
            eligibility_areas=[db.new_york_state],
        )
        # This library has no service areas.
        db.library("Nowhere Library")

        db.session.flush()

        [(lib, s)] = Library.relevant(db.session, (40.65, -73.94), "eng").most_common()
        assert lib == nypl

    def test_relevant_all_factors(self, db: DatabaseTransactionFixture):
        # This library serves the general public in NY state, with a focus on Manhattan.
        nypl = db.library(
            "New York Public Library",
            focus_areas=[db.crude_new_york_county],
            eligibility_areas=[db.new_york_state],
            audiences=[Audience.PUBLIC],
        )
        CollectionSummary.set(nypl, "eng", 150000)
        CollectionSummary.set(nypl, "spa", 20000)
        CollectionSummary.set(nypl, "rus", 5000)

        # This library serves the general public in NY state, with a focus on Brooklyn.
        bpl = db.library(
            "Brooklyn Public Library",
            focus_areas=[db.crude_kings_county],
            eligibility_areas=[db.new_york_state],
            audiences=[Audience.PUBLIC],
        )
        CollectionSummary.set(bpl, "eng", 75000)
        CollectionSummary.set(bpl, "spa", 10000)

        # This library serves the general public in Albany.
        albany = db.library(
            "Albany Public Library",
            focus_areas=[db.crude_albany],
            eligibility_areas=[db.crude_albany],
            audiences=[Audience.PUBLIC],
        )
        CollectionSummary.set(albany, "eng", 50000)
        CollectionSummary.set(albany, "spa", 5000)

        # This library serves NYU students.
        nyu_lib = db.library(
            "NYU Library",
            focus_areas=[db.new_york_city],
            eligibility_areas=[db.new_york_city],
            audiences=[Audience.EDUCATIONAL_SECONDARY],
        )
        CollectionSummary.set(nyu_lib, "eng", 100000)

        # These libraries serves the general public, but mostly academics.
        nyu_press = db.library(
            "NYU Press",
            focus_areas=[db.new_york_city],
            eligibility_areas=[Place.everywhere(db.session)],
            audiences=[Audience.RESEARCH, Audience.PUBLIC],
        )
        CollectionSummary.set(nyu_press, "eng", 40)

        unm = db.library(
            "UNM Press",
            focus_areas=[db.kansas_state],
            eligibility_areas=[Place.everywhere(db.session)],
            audiences=[Audience.RESEARCH, Audience.PUBLIC],
        )
        CollectionSummary.set(unm, "eng", 60)
        CollectionSummary.set(unm, "spa", 10)

        # This library serves people with print disabilities in the US.
        bard = db.library(
            "BARD",
            focus_areas=[db.crude_us],
            eligibility_areas=[db.crude_us],
            audiences=[Audience.PRINT_DISABILITY],
        )
        CollectionSummary.set(bard, "eng", 100000)

        # This library serves the general public everywhere.
        internet_archive = db.library(
            "Internet Archive",
            focus_areas=[Place.everywhere(db.session)],
            eligibility_areas=[Place.everywhere(db.session)],
            audiences=[Audience.PUBLIC],
        )
        CollectionSummary.set(internet_archive, "eng", 10000000)
        CollectionSummary.set(internet_archive, "spa", 1000)
        CollectionSummary.set(internet_archive, "rus", 1000)

        db.session.flush()

        # In Manhattan.
        libraries = Library.relevant(db.session, (40.75, -73.98), "eng").most_common()
        assert len(libraries) == 4
        assert [library[0] for library in libraries] == [
            nypl,
            bpl,
            internet_archive,
            nyu_press,
        ]

        # In Brooklyn.
        libraries = Library.relevant(db.session, (40.65, -73.94), "eng").most_common()
        assert len(libraries) == 4
        assert [library[0] for library in libraries] == [
            bpl,
            nypl,
            internet_archive,
            nyu_press,
        ]

        # In Queens.
        libraries = Library.relevant(db.session, (40.76, -73.91), "eng").most_common()
        assert len(libraries) == 4
        assert [library[0] for library in libraries] == [
            nypl,
            bpl,
            internet_archive,
            nyu_press,
        ]

        # In Albany.
        libraries = Library.relevant(db.session, (42.66, -73.77), "eng").most_common()
        assert len(libraries) == 5
        assert [library[0] for library in libraries] == [
            albany,
            nypl,
            bpl,
            internet_archive,
            nyu_press,
        ]

        # In Syracuse (200km west of Albany).
        libraries = Library.relevant(db.session, (43.06, -76.15), "eng").most_common()
        assert len(libraries) == 4
        assert [library[0] for library in libraries] == [
            nypl,
            bpl,
            internet_archive,
            nyu_press,
        ]

        # In New Jersey.
        libraries = Library.relevant(db.session, (40.79, -74.43), "eng").most_common()
        assert len(libraries) == 4
        assert [library[0] for library in libraries] == [
            nypl,
            bpl,
            internet_archive,
            nyu_press,
        ]

        # In Las Cruces, NM. Internet Archive is first at the moment
        # due to its large collection, but maybe it would be better if UNM was.
        libraries = Library.relevant(db.session, (32.32, -106.77), "eng").most_common()
        assert len(libraries) == 2
        assert {library[0] for library in libraries} == {unm, internet_archive}

        # Russian speaker in Albany. Albany doesn't pass the score threshold
        # since it didn't report having any Russian books, but maybe we should
        # consider the total collection size as well as the user's language.
        libraries = Library.relevant(db.session, (42.66, -73.77), "rus").most_common()
        assert len(libraries) == 2
        assert [library[0] for library in libraries] == [nypl, internet_archive]

        # Spanish speaker in Manhattan.
        libraries = Library.relevant(db.session, (40.75, -73.98), "spa").most_common()
        assert len(libraries) == 4
        assert [library[0] for library in libraries] == [
            nypl,
            bpl,
            internet_archive,
            unm,
        ]

        # Patron with a print disability in Manhattan.
        libraries = Library.relevant(
            db.session, (40.75, -73.98), "eng", audiences=[Audience.PRINT_DISABILITY]
        ).most_common()
        assert len(libraries) == 5
        assert [library[0] for library in libraries] == [
            bard,
            nypl,
            bpl,
            internet_archive,
            nyu_press,
        ]

    def test_nearby(self, db: DatabaseTransactionFixture):
        # Create two libraries. One serves New York City, and one serves
        # the entire state of Connecticut.
        nypl = db.library(
            "New York Public Library", eligibility_areas=[db.new_york_city]
        )
        ct_state = db.library(
            "Connecticut State Library", eligibility_areas=[db.connecticut_state]
        )

        # From this point in Brooklyn, NYPL is the closest library.
        # NYPL's service area includes that point, so the distance is
        # zero. The service area of CT State (i.e. the Connecticut
        # border) is only 44 kilometers away, so it also shows up.
        [(lib1, d1), (lib2, d2)] = Library.nearby(db.session, (40.65, -73.94))

        assert d1 == 0
        assert lib1 == nypl

        assert int(d2 / 1000) == 44
        assert lib2 == ct_state

        # From this point in Connecticut, CT State is the closest
        # library (0 km away), so it shows up first, but NYPL (61 km
        # away) also shows up as a possibility.
        [(lib1, d1), (lib2, d2)] = Library.nearby(db.session, (41.3, -73.3))
        assert lib1 == ct_state
        assert d1 == 0

        assert lib2 == nypl
        assert int(d2 / 1000) == 61

        # From this point in Pennsylvania, NYPL shows up (142km away) but
        # CT State does not.
        [(lib1, d1)] = Library.nearby(db.session, (40, -75.8))
        assert lib1 == nypl
        assert int(d1 / 1000) == 142

        # If we only look within a 100km radius, then there are no
        # libraries near that point in Pennsylvania.
        assert Library.nearby(db.session, (40, -75.8), 100).all() == []

        # By default, nearby() only finds libraries that are in production.
        def m(production):
            return Library.nearby(
                db.session, (41.3, -73.3), production=production
            ).count()

        # Take all the libraries we found earlier out of production.
        for library in ct_state, nypl:
            library.registry_stage = Library.TESTING_STAGE
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
        assert m("new york public library") == (
            "new york public library",
            "new york",
            None,
        )
        assert m("queens library") == ("queens library", "queens", None)
        assert m("kern county library") == ("kern county library", "kern", Place.COUNTY)
        assert m("new york state library") == (
            "new york state library",
            "new york",
            Place.STATE,
        )
        assert m("lapl") == ("lapl", "lapl", None)

    def test_search_by_library_name(self, db: DatabaseTransactionFixture):
        def search(name, here=None, **kwargs):
            return list(
                Library.search_by_library_name(db.session, name, here, **kwargs)
            )

        # The Brooklyn Public Library serves New York City.
        brooklyn = db.library(
            name="Brooklyn Public Library",
            focus_areas=[db.new_york_city, db.zip_11212],
        )

        # We can find the library by its name.
        assert search("brooklyn public library") == [brooklyn]

        # We can tolerate a small number of typos in a name or alias
        # that is longer than 6 characters.
        assert search("broklyn public library") == [brooklyn]
        get_one_or_create(
            db.session, LibraryAlias, name="Bklynlib", language=None, library=brooklyn
        )
        assert search("zklynlib") == [brooklyn]

        # The Boston Public Library serves Boston, MA.
        boston = db.library(name="Boston Public Library", focus_areas=[db.boston_ma])

        # Searching for part of the name--i.e. "boston" rather than "boston public library"--should work.
        assert search("boston") == [boston]

        # Both libraries are known colloquially as 'BPL'.
        for library in (brooklyn, boston):
            get_one_or_create(
                db.session, LibraryAlias, name="BPL", language=None, library=library
            )
        assert set(search("bpl")) == {brooklyn, boston}

        # We do not tolerate typos in short names, because the chance of
        # ambiguity is so high.
        assert search("opl") == []

        # If we're searching for "BPL" from California, Brooklyn shows
        # up first, because it's closer to California.
        assert [x[0].name for x in search("bpl", GeometryUtility.point(35, -118))] == [
            "Brooklyn Public Library",
            "Boston Public Library",
        ]

        # If we're searching for "BPL" from Maine, Boston shows
        # up first, because it's closer to Maine.
        assert [x[0].name for x in search("bpl", GeometryUtility.point(43, -70))] == [
            "Boston Public Library",
            "Brooklyn Public Library",
        ]

        # By default, search_by_library_name() only finds libraries
        # in production. Put them in the TESTING stage and they disappear.
        for library in (brooklyn, boston):
            library.registry_stage = Library.TESTING_STAGE
        assert search("bpl", production=True) == []

        # But you can find them by passing in production=False.
        assert len(search("bpl", production=False)) == 2

    def test_search_by_location(self, db: DatabaseTransactionFixture):
        # We know about three libraries.
        nypl = db.nypl
        kansas_state = db.kansas_state_library

        # The NYPL explicitly covers New York City, which has
        # 'Manhattan' as an alias.
        [nyc, zip_11212] = [x.place for x in nypl.service_areas]
        assert "Manhattan" in [x.name for x in nyc.aliases]

        # The Kansas state library covers the entire state,
        # which happens to contain a city called Manhattan.
        [kansas] = [x.place for x in kansas_state.service_areas]
        assert kansas.external_name == "Kansas"
        assert kansas.type == Place.STATE
        manhattan_ks = db.manhattan_ks  # noqa: F841

        # A search for 'manhattan' finds both libraries.
        libraries = list(Library.search_by_location_name(db.session, "manhattan"))
        assert {x.name for x in libraries} == {"NYPL", "Kansas State Library"}

        # If you're searching from California, the Kansas library
        # shows up first.
        ca_results = Library.search_by_location_name(
            db.session, "manhattan", here=GeometryUtility.point(35, -118)
        )
        assert [x[0].name for x in ca_results] == ["Kansas State Library", "NYPL"]

        # If you're searching from Maine, the New York library shows
        # up first.
        me_results = Library.search_by_location_name(
            db.session, "manhattan", here=GeometryUtility.point(43, -70)
        )
        assert [x[0].name for x in me_results] == ["NYPL", "Kansas State Library"]

        # We can insist that only certain types of places be considered as
        # matching the name. There is no state called 'Manhattan', so
        # this query finds nothing.
        excluded = Library.search_by_location_name(
            db.session, "manhattan", type=Place.STATE
        )
        assert excluded.all() == []

        # A search for "Brooklyn" finds the NYPL, but it only finds it
        # once, even though NYPL is associated with two places called
        # "Brooklyn": New York City and the ZIP code 11212
        [brooklyn_results] = Library.search_by_location_name(
            db.session, "brooklyn", here=GeometryUtility.point(43, -70)
        )
        assert brooklyn_results[0] == nypl

        nypl.registry_stage = Library.TESTING_STAGE
        assert (
            Library.search_by_location_name(
                db.session,
                "brooklyn",
                here=GeometryUtility.point(43, -70),
                production=True,
            ).all()
            == []
        )

        assert (
            Library.search_by_location_name(
                db.session,
                "brooklyn",
                here=GeometryUtility.point(43, -70),
                production=False,
            ).count()
            == 1
        )

    def test_search_within_description(self, db: DatabaseTransactionFixture):
        """Test searching for a phrase within a library's description."""
        library = db.library(
            name="Library With Description",
            description="We are giving this library a description for testing purposes.",
        )
        results = list(
            Library.search_within_description(db.session, "testing purposes")
        )
        assert results == [library]

    def test_search(self, db: DatabaseTransactionFixture):
        """Test the overall search method."""

        # Here's a Kansas library with a confusing name whose
        # Levenshtein distance from "New York" is 2.
        new_work = db.library(name="Now Work", focus_areas=[db.kansas_state])

        # Here's a library whose service area includes a place called
        # "New York".
        nypl = db.nypl  # noqa: F841

        libraries = Library.search(db.session, (40.7, -73.9), "NEW YORK")
        # Even though NYPL is closer to the current location, the
        # Kansas library showed up first because it was a name match,
        # as opposed to a service location match.
        assert [x[0].name for x in libraries] == ["Now Work", "NYPL"]
        assert [int(x[1] / 1000) for x in libraries] == [1768, 0]

        # This search query has a Levenshtein distance of 1 from "New
        # York", but a distance of 3 from "Now Work", so only NYPL
        # shows up.
        #
        # Although "NEW YORM" matches both the city and state, both of
        # which intersect with NYPL's service area, NYPL only shows up
        # once.
        libraries = Library.search(db.session, (40.7, -73.9), "NEW YORM")
        assert [x[0].name for x in libraries] == ["NYPL"]

        # Searching for a place name picks up libraries whose service
        # areas intersect with that place.
        libraries = Library.search(db.session, (40.7, -73.9), "Kansas")
        assert [x[0].name for x in libraries] == ["Now Work"]

        # By default, search() only finds libraries in production.
        db.nypl.registry_stage = Library.TESTING_STAGE
        new_work.registry_stage = Library.TESTING_STAGE

        def m(production):
            return len(
                Library.search(db.session, (40.7, -73.9), "New York", production)
            )

        assert m(True) == 0

        # But you can find libraries that are in the testing stage
        # by passing in production=False.
        assert m(False) == 2

    def test_search_excludes_duplicates(self, db: DatabaseTransactionFixture):
        # Here's a library that serves a place called Kansas
        # whose name is also "Kansas"
        library = db.library(name="Kansas", focus_areas=[db.kansas_state])
        # It matches both the name search and the location search.
        assert Library.search_by_location_name(db.session, "kansas").all() == [library]
        assert Library.search_by_library_name(db.session, "kansas").all() == [library]

        # But when we do the general search, the library only shows up once.
        [(result, distance)] = Library.search(db.session, (0, 0), "Kansas")
        assert result == library
