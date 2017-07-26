from collections import defaultdict
import json
from nose.tools import (
    assert_raises_regexp,
    assert_raises,
    eq_,
    set_trace,
)
from sqlalchemy.orm.exc import (
    MultipleResultsFound,
)
from authentication_document import AuthenticationDocument

# Alias for a long class name
AuthDoc = AuthenticationDocument

class MockPlace(object):
    """Used to test AuthenticationDocument.parse_coverage."""

    # Used to indicate that a place name is ambiguous.
    AMBIGUOUS = object()

    # Used to indicate coverage through the universe or through a
    # country.
    EVERYWHERE = object()
    
    def __init__(self, by_name=None, is_inside=None):
        self.by_name = by_name or dict()
        self.is_inside = is_inside or dict()

    def lookup_by_name(self, _db, name, place_type):
        place = self.by_name.get(name)
        if place is self.AMBIGUOUS:
            raise MultipleResultsFound()
        return place
        
    def lookup_inside(self, _db, name, must_be_inside):
        places_inside = self.is_inside.get(must_be_inside)
        place = places_inside.get(name)
        if place is self.AMBIGUOUS:
            raise MultipleResultsFound()
        return place

    def everywhere(self, _db):
        return self.EVERYWHERE

class TestParseCoverage(object):

    EVERYWHERE = AuthenticationDocument.COVERAGE_EVERYWHERE
    
    def parse_places(self, mock_place, coverage_object, expected_places=None,
                     expected_unknown=None, expected_ambiguous=None):
        """Call AuthenticationDocument.parse_coverage. Verify that the parsed
        list of places, as well as the dictionaries of unknown and
        ambiguous place names, are as expected.
        """
        place_objs, unknown, ambiguous = AuthDoc.parse_coverage(
            None, coverage_object, mock_place
        )
        empty = defaultdict(list)
        expected_places = expected_places or []
        expected_unknown = expected_unknown or empty
        expected_ambiguous = expected_ambiguous or empty
        eq_(sorted(expected_places), sorted(place_objs))
        eq_(expected_unknown, unknown)
        eq_(expected_ambiguous, ambiguous)
        
    def test_universal_coverage(self):
        """Test an authentication document that says a library covers the
        whole universe.
        """
        places = MockPlace()
        self.parse_places(
            places, self.EVERYWHERE, [MockPlace.EVERYWHERE]
        )

    def test_entire_country(self):
        """Test an authentication document that says a library covers an
        entire country.
        """
        places = MockPlace({"US": "United States"})
        self.parse_places(
            places,
            {"US": self.EVERYWHERE },
            expected_places=["United States"]
        )

    def test_ambiguous_country(self):
        """Test the unlikely scenario where an authentication document says a
        library covers an entire country, but it's ambiguous which
        country is being referred to.
        """

        places = MockPlace(
            {"CA": "Canada", "Europe I think?": MockPlace.AMBIGUOUS}
        )
        self.parse_places(
            places, 
            {"Europe I think?": self.EVERYWHERE, "CA": self.EVERYWHERE },
            expected_places=["Canada"],
            expected_ambiguous={"Europe I think?": self.EVERYWHERE}
        )

    def test_unknown_country(self):
        """Test an authentication document that says a library covers an
        entire country, but the library registry doesn't know anything about
        that country's geography.
        """

        places = MockPlace({"CA": "Canada"})
        self.parse_places(
            places, 
            {"Memory Alpha": self.EVERYWHERE, "CA": self.EVERYWHERE },
            expected_places=["Canada"],
            expected_unknown={"Memory Alpha": self.EVERYWHERE}
        )

    def test_places_within_country(self):
        """Test an authentication document that says a library
        covers one or more places within a country.
        """
        # This authentication document covers two places called
        # "San Francisco" (one in the US and one in Mexico) as well as a
        # place called "Mexico City" in Mexico.
        #
        # Note that it's invalid to map a country name to a single
        # place name (it's supposed to always be a list), but our
        # parser can handle it.
        doc = {"US": "San Francisco", "MX": ["San Francisco", "Mexico City"]}
        
        is_inside = {
            "US": {"San Francisco": "object1", "San Jose": "object2"},
            "MX": {"San Francisco": "object3", "Mexico City": "object4"}
        }
        places = MockPlace(is_inside=is_inside)

        # AuthenticationDocument.parse_coverage is able to turn those
        # three place names into place objects.
        self.parse_places(
            places, doc,
            expected_places=["object1", "object3", "object4"]
        )

    def test_ambiguous_place_within_country(self):
        """Test an authentication document that names an ambiguous
        place within a country.
        """
        is_inside = {"US": {"Springfield": MockPlace.AMBIGUOUS}}
        places = MockPlace(is_inside=is_inside)
        self.parse_places(
            places, {"US": ["Springfield"]},
            expected_ambiguous={"US": ["Springfield"]}
        )

    def test_unknown_place_within_country(self):
        """Test an authentication document that names an unknown
        place within a country.
        """
        is_inside = {"US": {"San Francisco": "object1"}}
        places = MockPlace(is_inside=is_inside)
        self.parse_places(
            places, {"US": "Nowheresville"},
            expected_unknown={"US": ["Nowheresville"]}
        )
