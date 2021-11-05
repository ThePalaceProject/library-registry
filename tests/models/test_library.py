import random
import re
import uuid

import pytest

from library_registry.constants import LibraryType
from library_registry.model import (
    DelegatedPatronIdentifier,
    Hyperlink,
    Library,
    LibraryAlias,
    Place,
)
from library_registry.model_helpers import get_one_or_create
from library_registry.util import GeometryUtility


GENERATED_SHORT_NAME_REGEX = re.compile(r'^[A-Z]{6}$')


class TestLibraryModel:
    ##### Public Method Tests ################################################  # noqa: E266

    def test_set_hyperlink_exceptions(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: An existing Library
        WHEN:  The .set_hyperlink() method is called without all necessary parameters
        THEN:  Appropriate exceptions should be raised
        """
        library = create_test_library(db_session)

        with pytest.raises(ValueError) as exc:
            library.set_hyperlink("rel")
        assert "No Hyperlink hrefs were specified" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            library.set_hyperlink(None, ["href"])
        assert "No link relation was specified" in str(exc.value)

        destroy_test_library(db_session, library)

    def test_set_hyperlink(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: An existing Library object
        WHEN:  .set_hyperlink is called with sufficient arguments
        THEN:  A Hyperlink object should be returned, with is_modified True
        """
        library = create_test_library(db_session)
        (link, is_modified) = library.set_hyperlink("rel", "href1", "href2")
        assert isinstance(link, Hyperlink)
        assert is_modified is True
        assert link.rel == "rel"
        assert link.href == "href1"
        assert link.library_id == library.id

        destroy_test_library(db_session, library)

    def test_set_hyperlink_multiple_calls(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: An existing Library object
        WHEN:  .set_hyperlink is called multiple times, with href parameters in different orders
        THEN:  The href set as default in the original link creation will remain the return value of .href
        """
        library = create_test_library(db_session)
        (link_original, _) = library.set_hyperlink("rel", "href1", "href2")
        # Calling set_hyperlink again does not modify the link so long as the old href is still a possibility.
        (link_new, is_modified) = library.set_hyperlink("rel", "href2", "href1")
        assert link_original == link_new
        assert link_new.rel == "rel"
        assert link_new.href == "href1"
        assert is_modified is False

        destroy_test_library(db_session, library)

    def test_set_hyperlink_overwrite_href(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: An existing Library object with a hyperlink with a specific href value
        WHEN:  A subsequent call to .set_hyperlink() provides hrefs which do not include the existing href value
        THEN:  The .href of that Hyperlink will be set to the first of the new values
        """
        library = create_test_library(db_session)
        (link_original, _) = library.set_hyperlink("rel", "href1", "href2")
        (link_modified, is_modified) = library.set_hyperlink("rel", "href2", "href3")
        assert is_modified is True
        assert link_original == link_modified
        assert link_modified.rel == "rel"
        assert link_modified.href == "href2"

        destroy_test_library(db_session, library)

    def test_set_hyperlink_one_link_rel_per_library(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: An existing Library object with a hyperlink for a specific rel name
        WHEN:  A second call to .set_hyperlink() is made with the same rel name
        THEN:  The existing hyperlink is either returned or modified--there is never more
               than one hyperlink for a given rel at the same Library
        """
        library = create_test_library(db_session)
        (link_original, _) = library.set_hyperlink("rel", "href1", "href2")
        (link_modified, _) = library.set_hyperlink("rel", "href2", "href3")

        assert library.hyperlinks == [link_modified]

        destroy_test_library(db_session, library)

    def test_set_hyperlink_multiple_hyperlinks_same_resource(
        self, db_session, create_test_library, destroy_test_library
    ):
        """
        GIVEN: An existing Library object with a hyperlink for a specific rel name
        WHEN:  A second call to .set_hyperlink() is made, for the same resource but a different rel name
        THEN:  A second hyperlink should be created
        """
        library = create_test_library(db_session)
        (link_original, _) = library.set_hyperlink("rel_alpha", "href1")
        (link_new, modified) = library.set_hyperlink("rel_bravo", "href1")
        assert link_original.resource == link_new.resource
        assert modified is True

        destroy_test_library(db_session, library)

    def test_set_hyperlink_two_libraries_link_same_resource_same_rel(
        self, db_session, create_test_library, destroy_test_library
    ):
        """
        GIVEN: Two different Library objects:
                - One with an existing hyperlink to a specific rel/resource
                - One without a hyperlink to that rel/resource
        WHEN:  .set_hyperlink() is called for the second library with the same rel/resource
        THEN:  A Hyperlink is successfully created for the second library, with an identical
               rel/resource as for the first library
        """
        link_args = ["some-rel-name", "href-to-resource-001"]
        library_alpha = create_test_library(db_session)
        library_bravo = create_test_library(db_session)
        (link_alpha, is_alpha_modified) = library_alpha.set_hyperlink(*link_args)
        assert isinstance(link_alpha, Hyperlink)
        assert is_alpha_modified is True
        assert link_alpha.library_id == library_alpha.id

        (link_bravo, is_bravo_modified) = library_bravo.set_hyperlink(*link_args)
        assert isinstance(link_bravo, Hyperlink)
        assert is_bravo_modified is True
        assert link_bravo.library_id == library_bravo.id

        assert link_alpha.href == link_bravo.href
        assert link_alpha.rel == link_bravo.rel

        for library_obj in [library_alpha, library_bravo]:
            destroy_test_library(db_session, library_obj)

    ##### Private Method Tests ################################################  # noqa: E266

    ##### Field Validator Tests ###############################################  # noqa: E266

    def test_short_name_validation(self, nypl):
        """
        GIVEN: An existing Library object
        WHEN:  The .short_name field of that object is set to a string containing a pipe
        THEN:  A ValueError is raised
        """
        with pytest.raises(ValueError) as exc:
            nypl.short_name = "ab|cd"
        assert "Short name cannot contain the pipe character" in str(exc.value)

    ##### Property Method Tests ##############################################  # noqa: E266

    def test_set_library_stage(self, nypl):
        """
        GIVEN: An existing Library that the registry has put in production
        WHEN:  An attempt is made to set the .library_stage for that Library
        THEN:  A ValueError should be raised, because the .registry_stage gates .library_stage
        """
        # The .library_stage may not be changed while .registry_stage is PRODUCTION_STAGE
        with pytest.raises(ValueError) as exc:
            nypl.library_stage = Library.TESTING_STAGE
        assert "This library is already in production" in str(exc.value)

        # Have the registry take the library out of production.
        nypl.registry_stage = Library.CANCELLED_STAGE
        assert nypl.in_production is False

        # Now we can change the library stage however we want.
        nypl.library_stage = Library.TESTING_STAGE
        assert nypl.library_stage == Library.TESTING_STAGE
        nypl.library_stage = Library.CANCELLED_STAGE
        assert nypl.library_stage == Library.CANCELLED_STAGE
        nypl.library_stage = Library.PRODUCTION_STAGE
        assert nypl.library_stage == Library.PRODUCTION_STAGE

    def test_in_production(self, nypl):
        """
        GIVEN: An existing Library in PRODUCTION_STAGE
        WHEN:  Either .registry_stage or .library_stage is set to CANCELLED_STAGE
        THEN:  The Library's .in_production property should return False
        """
        assert nypl.library_stage == Library.PRODUCTION_STAGE
        assert nypl.registry_stage == Library.PRODUCTION_STAGE
        assert nypl.in_production is True

        # If either library_stage or registry stage is not
        # PRODUCTION_STAGE, we are not in production.
        nypl.registry_stage = Library.CANCELLED_STAGE
        assert nypl.in_production is False

        nypl.library_stage = Library.CANCELLED_STAGE
        assert nypl.in_production is False

        nypl.registry_stage = Library.PRODUCTION_STAGE
        assert nypl.in_production is False

    def test_number_of_patrons(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: A newly created Library in Production stage
        WHEN:  A DelegatedPatronIdentifier with an Adobe Account ID is associated with that Library
        THEN:  The Library's .number_of_patrons property should reflect that patron
        """
        library = create_test_library(db_session)

        assert library.number_of_patrons == 0
        (identifier, _) = DelegatedPatronIdentifier.get_one_or_create(
            db_session, library, str(uuid.uuid4()), DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID, None
        )
        assert library.number_of_patrons == 1

        destroy_test_library(db_session, library)

    def test_number_of_patrons_non_adobe(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: A newly created Library in Production stage
        WHEN:  A DelegatedPatronIdentifier without an Adobe Account ID is associated with that Library
        THEN:  The Library's .number_of_patrons property should not increase
        """
        library = create_test_library(db_session)

        (identifier, _) = DelegatedPatronIdentifier.get_one_or_create(
            db_session, library, str(uuid.uuid4()), "abc", None
        )
        assert library.number_of_patrons == 0

        destroy_test_library(db_session, library)

    def test_number_of_patrons_non_production_stage(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: A newly created Library in Testing stage
        WHEN:  A DelegatedPatronIdentifier is created referencing that Library
        THEN:  The Library's .number_of_patrons property should not increase, since identifiers
               cannot be assigned to libraries not in production.
        """
        library = create_test_library(db_session, library_stage=Library.TESTING_STAGE)

        (identifier, _) = DelegatedPatronIdentifier.get_one_or_create(
            db_session, library, str(uuid.uuid4()), DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID, None
        )
        assert library.number_of_patrons == 0

        destroy_test_library(db_session, library)

    def test_service_area_single(self, db_session, create_test_library, create_test_place):
        """
        GIVEN: An existing Place object
        WHEN:  A Library is created with that Place as the contents of the list passed to the
               Library constructor's eligibility_areas parameter
        THEN:  That Place should be the sole entry in the list returned by .service_areas
        """
        a_place = create_test_place(db_session)
        a_library = create_test_library(db_session, eligibility_areas=[a_place])
        [service_area] = a_library.service_areas
        assert service_area.place == a_place
        assert service_area.library == a_library

        db_session.delete(a_library)
        db_session.delete(a_place)
        db_session.commit()

    def test_service_area_multiple(self, db_session, create_test_library, create_test_place, destroy_test_library):
        """
        GIVEN: A Library with multiple service areas
        WHEN:  That Library instance's .service_area property is accessed
        THEN:  None should be returned
        """
        (place_alpha, place_bravo) = [create_test_place(db_session) for _ in range(2)]
        library = create_test_library(db_session, eligibility_areas=[place_alpha, place_bravo])
        assert library.service_area is None
        destroy_test_library(db_session, library)
        for place_obj in [place_alpha, place_bravo]:
            db_session.delete(place_obj)
        db_session.commit()

    def test_service_area_everywhere(
        self, db_session, create_test_library, create_test_place, destroy_test_library
    ):
        """
        GIVEN: A Library with one service area, of type Place.EVERYWHERE
        WHEN:  That Library instance's .service_area property is accessed
        THEN:  The Everywhere place should be returned
        """
        everywhere = create_test_place(db_session, place_type=Place.EVERYWHERE)
        library = create_test_library(db_session, eligibility_areas=[everywhere])
        assert library.service_area is everywhere
        destroy_test_library(db_session, library)
        db_session.delete(everywhere)
        db_session.commit()

    def test_service_area_name(
        self, db_session, create_test_library, create_test_place, destroy_test_library
    ):
        """
        GIVEN: A Library with one service area, with a human friendly name
        WHEN:  That Library instance's .service_area_name property is accessed
        THEN:  The human friendly name of the service area place should be returned
        """
        place = create_test_place(db_session, place_type=Place.CITY, external_name="Smalltown")
        library = create_test_library(db_session, eligibility_areas=[place])
        expected = place.human_friendly_name
        assert library.service_area_name == expected
        destroy_test_library(db_session, library)
        db_session.delete(place)
        db_session.commit()

    def test_types(
        self, db_session, create_test_place, create_test_library, zip_10018,
        new_york_city, new_york_state, destroy_test_library
    ):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        postal = zip_10018
        city = new_york_city
        state = new_york_state
        nation = create_test_place(db_session, external_id='CA', external_name='Canada',
                                   place_type=Place.NATION, abbreviated_name='CA')
        province = create_test_place(db_session, external_id="MB", external_name="Manitoba",
                                     place_type=Place.STATE, abbreviated_name="MB", parent=nation)
        everywhere = Place.everywhere(db_session)

        # Libraries with different kinds of service areas are given different types.
        for focus, type in (
            (postal, LibraryType.LOCAL),
            (city, LibraryType.LOCAL),
            (state, LibraryType.STATE),
            (province, LibraryType.PROVINCE),
            (nation, LibraryType.NATIONAL),
            (everywhere, LibraryType.UNIVERSAL)
        ):
            library = create_test_library(db_session, focus_areas=[focus])
            assert focus.library_type == type
            assert [type] == list(library.types)
            destroy_test_library(db_session, library)

        # If a library's service area is ambiguous, it has no service area-related type.
        library = create_test_library(db_session, library_name="library", focus_areas=[postal, province])
        assert [] == list(library.types)
        destroy_test_library(db_session, library)
        db_session.delete(nation)
        db_session.delete(province)
        db_session.commit()

    ##### Public Class Method Tests ##########################################  # noqa: E266

    def test_for_short_name(self, db_session, nypl):
        """
        GIVEN: An existing Library with a given short_name value
        WHEN:  The Library.for_short_name() class method is called with that short_name value
        THEN:  The appropriate Library object should be returned
        """
        assert Library.for_short_name(db_session, 'NYPL') == nypl

    def test_for_urn(self, db_session, nypl):
        """
        GIVEN: An existing library with a given internal_urn value
        WHEN:  The Library.for_urn() class method is called with that internal_urn value
        THEN:  The appropriate Library object should be returned
        """
        assert Library.for_urn(db_session, nypl.internal_urn) == nypl

    def test_random_short_name(self):
        """
        GIVEN: A pre-determined seed for the Python random library
        WHEN:  The Library.random_short_name() class method is called
        THEN:  A seed-determined value or values are generated which are six ascii uppercase characters
        """
        random.seed(42)
        SEED_42_FIRST_VALUE = "UDAXIH"
        generated_name = Library.random_short_name()
        assert generated_name == SEED_42_FIRST_VALUE
        assert re.match

    def test_random_short_name_duplicate_check(self):
        """
        GIVEN: A duplicate check function indicating a seeded name is already in use
        WHEN:  The Library.random_short_name() function is called with that function
        THEN:  The next seeded name value should be returned
        """
        random.seed(42)
        SEED_42_FIRST_VALUE = "UDAXIH"
        SEED_42_SECOND_VALUE = "HEXDVX"

        assert Library.random_short_name() == SEED_42_FIRST_VALUE     # Call once to move past initial value
        name = Library.random_short_name(duplicate_check=lambda x: x == SEED_42_FIRST_VALUE)
        assert name == SEED_42_SECOND_VALUE

    def test_random_short_name_quit_after_20_attempts(self):
        """
        GIVEN: A duplicate check function which always indicates a duplicate name exists
        WHEN:  Library.random_short_name() is called with that duplicate check
        THEN:  A ValueError should be raised indicating no short name could be generated
        """
        with pytest.raises(ValueError) as exc:
            Library.random_short_name(duplicate_check=lambda x: True)
        assert "Could not generate random short name after 20 attempts!" in str(exc.value)

    def test_get_hyperlink(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: An existing Library object
        WHEN:  A hyperlink is created associated with that Library for a given rel name
        THEN:  A subsequent call to Library.get_hyperlink() referencing that Library and
               rel name should return an appropriate Hyperlink object
        """
        library = create_test_library(db_session)
        (link1, _) = library.set_hyperlink("contact_email", "contact_href")
        (link2, _) = library.set_hyperlink("help_email", "help_href")

        contact_link = Library.get_hyperlink(library, "contact_email")
        assert isinstance(contact_link, Hyperlink)
        assert link1 == contact_link

        help_link = Library.get_hyperlink(library, "help_email")
        assert isinstance(help_link, Hyperlink)
        assert link2 == help_link

        destroy_test_library(db_session, library)

    def test_patron_counts_by_library(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: Multiple existing Libraries, each with some number of patrons
        WHEN:  Library.patron_counts_by_library() is passed a list of instances representing those Libraries
        THEN:  A dictionary should be returned with library_id: count entries
        """
        library_alpha = create_test_library(db_session)
        library_bravo = create_test_library(db_session)
        library_charlie = create_test_library(db_session)

        # Assign two patrons to library alpha
        for user_string in ('alpha', 'bravo'):
            DelegatedPatronIdentifier.get_one_or_create(
                db_session, library_alpha, user_string, DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID, None
            )

        # Assign three patrons to library bravo
        for user_string in ('charlie', 'delta', 'echo'):
            DelegatedPatronIdentifier.get_one_or_create(
                db_session, library_bravo, user_string, DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID, None
            )

        counts = Library.patron_counts_by_library(db_session, [library_alpha, library_bravo, library_charlie])
        assert counts == {
            library_alpha.id: 2,
            library_bravo.id: 3,
        }

        for library_obj in (library_alpha, library_bravo, library_charlie):
            destroy_test_library(db_session, library_obj)

    @pytest.mark.needsdocstring
    def test_nearby(
        self, db_session, nypl, connecticut_state_library
    ):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        # From this point in Brooklyn, NYPL is the closest library.
        # NYPL's service area includes that point, so the distance is
        # zero. The service area of CT State (i.e. the Connecticut
        # border) is only 44 kilometers away, so it also shows up.
        [(lib1, d1), (lib2, d2)] = Library.nearby(db_session, (40.65, -73.94))

        assert d1 == 0
        assert lib1 == nypl

        assert int(d2/1000) == 44
        assert lib2 == connecticut_state_library

        # From this point in Connecticut, CT State is the closest
        # library (0 km away), so it shows up first, but NYPL (61 km
        # away) also shows up as a possibility.
        [(lib1, d1), (lib2, d2)] = Library.nearby(db_session, (41.3, -73.3))
        assert lib1 == connecticut_state_library
        assert d1 == 0

        assert lib2 == nypl
        assert int(d2/1000) == 61

        # From this point in Pennsylvania, NYPL shows up (142km away) but
        # CT State does not.
        [(lib1, d1)] = Library.nearby(db_session, (40, -75.8))
        assert lib1 == nypl
        assert int(d1/1000) == 142

        # If we only look within a 100km radius, then there are no
        # libraries near that point in Pennsylvania.
        assert Library.nearby(db_session, (40, -75.8), 100).all() == []

        # By default, nearby() only finds libraries that are in production.
        def m(production):
            return Library.nearby(db_session, (41.3, -73.3), production=production).count()

        # Take all the libraries we found earlier out of production.
        for lib in (connecticut_state_library, nypl):
            lib.registry_stage = Library.TESTING_STAGE

        # Now there are no results.
        assert m(True) == 0

        # But we can run a search that includes libraries in the TESTING stage.
        assert m(False) == 2

    @pytest.mark.needsdocstring
    @pytest.mark.parametrize(
        "input,output",
        [
            pytest.param("THE LIBRARY", "the library"),
            pytest.param("\tthe   library\n\n", "the library"),
            pytest.param("the libary", "the library"),
        ]
    )
    def test_query_cleanup(self, input, output):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        assert Library.query_cleanup(input) == output

    @pytest.mark.needsdocstring
    @pytest.mark.parametrize(
        "input,output",
        [
            pytest.param("93203", "93203", id="us_zip"),
            pytest.param("93203-1234", "93203", id="us_zip_plus_four"),
            pytest.param("the library", None, id="non_postcode_string"),
            pytest.param("AB1 0AA", None, id="uk_post_code"),
        ]
    )
    def test_as_postal_code(self, input, output):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        assert Library.as_postal_code(input) == output

    @pytest.mark.needsdocstring
    @pytest.mark.parametrize(
        "input,output",
        [
            pytest.param("93203", (None, "93203", Place.POSTAL_CODE), id="us_zip"),
            pytest.param("new york public library", ("new york public library", "new york", None), id="nypl"),
            pytest.param("queens library", ("queens library", "queens", None), id="queens_library"),
            pytest.param("kern county library", ("kern county library", "kern", Place.COUNTY), id="kern_county"),
            pytest.param("new york state library", ("new york state library", "new york", Place.STATE), id="ny_state"),
            pytest.param("lapl", ("lapl", "lapl", None), id="lapl"),
        ]
    )
    def test_query_parts(self, input, output):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        assert Library.query_parts(input) == output

    @pytest.mark.needsdocstring
    def test_search_by_library_name(
        self, db_session, create_test_library, new_york_city, zip_11212, boston_ma, destroy_test_library
    ):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        def search(name, here=None, **kwargs):
            return list(Library.search_by_library_name(db_session, name, here, **kwargs))

        # The Brooklyn Public Library serves New York City.
        brooklyn = create_test_library(
            db_session, library_name="Brooklyn Public Library", focus_areas=[new_york_city, zip_11212]
        )

        # We can find the library by its name.
        assert search("brooklyn public library") == [brooklyn]

        # We can tolerate a small number of typos in a name or alias that is longer than 6 characters.
        assert search("broklyn public library") == [brooklyn]

        get_one_or_create(db_session, LibraryAlias, name="Bklynlib", language=None, library=brooklyn)
        assert search("zklynlib") == [brooklyn]

        # The Boston Public Library serves Boston, MA.
        boston = create_test_library(
            db_session, library_name="Boston Public Library", focus_areas=[boston_ma]
        )

        # Searching for part of the name--i.e. "boston" rather than "boston public library"--should work.
        assert search("boston") == [boston]

        # Both libraries are known colloquially as 'BPL'.
        for library in (brooklyn, boston):
            get_one_or_create(db_session, LibraryAlias, name="BPL", language=None, library=library)

        assert set(search("bpl")) == set([brooklyn, boston])

        # We do not tolerate typos in short names, because the chance of ambiguity is so high.
        assert search("opl") == []

        # If we're searching for "BPL" from California, Brooklyn shows up first, because it's closer to California.
        expected = ["Brooklyn Public Library", "Boston Public Library"]
        assert [x[0].name for x in search("bpl", GeometryUtility.point(35, -118))] == expected

        # If we're searching for "BPL" from Maine, Boston shows up first, because it's closer to Maine.
        expected = ["Boston Public Library", "Brooklyn Public Library"]
        assert [x[0].name for x in search("bpl", GeometryUtility.point(43, -70))] == expected

        # By default, search_by_library_name() only finds libraries in production.
        # Put them in the TESTING stage and they disappear.
        for lib in (brooklyn, boston):
            lib.registry_stage = Library.TESTING_STAGE

        assert search("bpl", production=True) == []

        # But you can find them by passing in production=False.
        assert len(search("bpl", production=False)) == 2

        for lib in (brooklyn, boston):
            destroy_test_library(db_session, lib)

    @pytest.mark.needsdocstring
    def test_search_by_location(
        self, db_session, nypl, kansas_state_library, connecticut_state_library, manhattan_ks
    ):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        # The NYPL explicitly covers New York City, which has 'Manhattan' as an alias.
        [nyc, zip_11212] = [x.place for x in nypl.service_areas]
        assert "Manhattan" in [x.name for x in nyc.aliases]

        # The Kansas state library covers the entire state, which happens to contain a city called Manhattan.
        [kansas, manhattan_kansas] = [x.place for x in kansas_state_library.service_areas]
        assert kansas.external_name == "Kansas"
        assert kansas.type == Place.STATE
        assert manhattan_kansas.type == Place.CITY

        # A search for 'manhattan' finds both libraries.
        libraries = list(Library.search_by_location_name(db_session, "manhattan"))
        assert set([x.name for x in libraries]) == set(["NYPL", "Kansas State Library"])

        # If you're searching from California, the Kansas library shows up first.
        ca_results = Library.search_by_location_name(db_session, "manhattan", here=GeometryUtility.point(35, -118))
        assert [x[0].name for x in ca_results] == ["Kansas State Library", "NYPL"]

        # If you're searching from Maine, the New York library shows up first.
        me_results = Library.search_by_location_name(db_session, "manhattan", here=GeometryUtility.point(43, -70))
        assert [x[0].name for x in me_results] == ["NYPL", "Kansas State Library"]

        # We can insist that only certain types of places be considered as matching the name.
        # There is no state called 'Manhattan', so this query finds nothing.
        excluded = Library.search_by_location_name(db_session, "manhattan", type=Place.STATE)
        assert excluded.all() == []

        # A search for "Brooklyn" finds the NYPL, but it only finds it once, even though NYPL is
        # associated with two places called "Brooklyn": New York City and the ZIP code 11212
        [brooklyn_results] = Library.search_by_location_name(
            db_session, "brooklyn", here=GeometryUtility.point(43, -70)
        )
        assert brooklyn_results[0] == nypl

        nypl.registry_stage = Library.TESTING_STAGE
        assert Library.search_by_location_name(
            db_session, "brooklyn", here=GeometryUtility.point(43, -70), production=True
        ).all() == []

        assert Library.search_by_location_name(
            db_session, "brooklyn", here=GeometryUtility.point(43, -70), production=False
        ).count() == 1

    @pytest.mark.needsdocstring
    def test_search_within_description(self, db_session, create_test_library, destroy_test_library):
        """
        Test searching for a phrase within a library's description.

        GIVEN:
        WHEN:
        THEN:
        """
        library = create_test_library(
            db_session,
            library_name="Library With Description",
            description="We are giving this library a description for testing purposes."
        )
        results = list(Library.search_within_description(db_session, "testing purposes"))
        assert results == [library]

        destroy_test_library(db_session, library)

    @pytest.mark.needsdocstring
    def test_search(self, db_session, create_test_library, destroy_test_library, kansas_state, nypl):
        """
        Test the overall search method.

        GIVEN:
        WHEN:
        THEN:
        """
        # Here's a Kansas library with a confusing name whose Levenshtein distance from "New York" is 2.
        new_work = create_test_library(db_session, library_name="Now Work", focus_areas=[kansas_state])

        # NYPL's service area includes a place called "New York".
        libraries = Library.search(db_session, (40.7, -73.9), "NEW YORK")

        # Even though NYPL is closer to the current location, the Kansas library showed up first
        # because it was a name match, as opposed to a service location match.
        assert set([x[0].name for x in libraries]) == set(['Now Work', 'NYPL'])
        assert set([int(x[1]/1000) for x in libraries]) == set([1768, 0])

        # This search query has a Levenshtein distance of 1 from "New York", but a distance of 3
        # from "Now Work", so only NYPL shows up.
        #
        # Although "NEW YORM" matches both the city and state, both of which intersect with NYPL's
        # service area, NYPL only shows up once.
        libraries = Library.search(db_session, (40.7, -73.9), "NEW YORM")
        assert [x[0].name for x in libraries] == ['NYPL']

        # Searching for a place name picks up libraries whose service areas intersect with that place.
        libraries = Library.search(db_session, (40.7, -73.9), "Kansas")
        assert [x[0].name for x in libraries] == ['Now Work']

        # By default, search() only finds libraries in production.
        nypl.registry_stage = Library.TESTING_STAGE
        new_work.registry_stage = Library.TESTING_STAGE

        def m(production):
            return len(
                Library.search(
                    db_session, (40.7, -73.9), "New York", production
                )
            )

        assert m(True) == 0

        # But you can find libraries that are in the testing stage by passing in production=False.
        assert m(False) == 2

        destroy_test_library(db_session, new_work)

    @pytest.mark.needsdocstring
    def test_search_excludes_duplicates(
        self, db_session, create_test_library, destroy_test_library, kansas_state
    ):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        # Here's a library that serves a place called Kansas whose name is also "Kansas"
        library = create_test_library(db_session, library_name="Kansas", focus_areas=[kansas_state])
        # It matches both the name search and the location search.
        assert Library.search_by_location_name(db_session, "kansas").all() == [library]
        assert Library.search_by_library_name(db_session, "kansas").all() == [library]

        # But when we do the general search, the library only shows up once.
        [(result, distance)] = Library.search(db_session, (0, 0), "Kansas")
        assert result == library

        destroy_test_library(db_session, library)

    ##### Private Class Method Tests ##########################################  # noqa: E266

    def test__feed_restriction_production_stage(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: A Library object whose .registry_stage and .library_stage are both PRODUCTION_STAGE
        WHEN:  The Library._feed_restriction() method is used to filter a Library query
        THEN:  That Production library should be in the result set no matter what boolean value is
               passed to _feed_restriction()
        """
        library = create_test_library(db_session)

        assert library.library_stage == Library.PRODUCTION_STAGE
        assert library.registry_stage == Library.PRODUCTION_STAGE

        # A library in PRODUCTION_STAGE should not be removed by feed restriction
        q = db_session.query(Library)
        assert q.filter(Library._feed_restriction(production=True)).all() == [library]
        assert q.filter(Library._feed_restriction(production=False)).all() == [library]

        destroy_test_library(db_session, library)

    def test__feed_restriction_mixed_stages(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: A Library object with:
                - .registry_stage set to TESTING_STAGE
                - .library_stage set to PRODUCTION_STAGE
        WHEN:  The Library._feed_restriction() method is used to filter a Library query
        THEN:  The Library should only be returned when the 'production' parameter for
               _feed_restriction() is set to False
        """
        library = create_test_library(db_session)
        library.registry_stage = Library.TESTING_STAGE

        q = db_session.query(Library)
        assert library.registry_stage != library.library_stage
        assert q.filter(Library._feed_restriction(production=True)).all() == []
        assert q.filter(Library._feed_restriction(production=False)).all() == [library]

        destroy_test_library(db_session, library)

    def test__feed_restriction_testing_stage(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: A Library object in TESTING_STAGE for both .library_stage and .registry_stage
        WHEN:  The Library._feed_restriction() method is used to filter a Library query
        THEN:  The Library should be returned in a testing feed, but not a production feed
        """
        library = create_test_library(db_session)
        library.registry_stage = Library.TESTING_STAGE
        library.library_stage = Library.TESTING_STAGE

        q = db_session.query(Library)
        assert q.filter(Library._feed_restriction(production=True)).all() == []
        assert q.filter(Library._feed_restriction(production=False)).all() == [library]

        destroy_test_library(db_session, library)

    def test__feed_restriction_cancelled_stage(self, db_session, create_test_library, destroy_test_library):
        """
        GIVEN: A Library object in CANCELLED_STAGE (for either or both of registry_stage/library_stage)
        WHEN:  The Library._feed_restriction() method is used to filter a Library query
        THEN:  The Library should not be returned in either testing or production feeds
        """
        library = create_test_library(db_session)
        library.registry_stage = Library.CANCELLED_STAGE
        library.library_stage = Library.CANCELLED_STAGE
        q = db_session.query(Library)
        assert q.filter(Library._feed_restriction(production=True)).all() == []
        assert q.filter(Library._feed_restriction(production=False)).all() == []

        destroy_test_library(db_session, library)
