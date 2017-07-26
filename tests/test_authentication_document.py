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


class TestLinkExtractor(object):
    """Test the _extract_link helper method."""

    def test_no_matching_link(self):
        links = {'alternate': [dict(href="http://foo/", type="text/html")]}

        # There is no link with the given relation.
        eq_(None, AuthDoc._extract_link(links, 'self'))

        # There is a link with the given relation, but the type is wrong.
        eq_(
            None,
            AuthDoc._extract_link(
                links, 'alternate', require_type="text/plain"
            )
        )
        

    def test_prefer_type(self):
        """Test that prefer_type holds out for the link you're
        looking for.
        """
        first_link = dict(href="http://foo/", type="text/html")
        second_link = dict(href="http://bar/", type="text/plain;charset=utf-8")
        links = {'alternate': [first_link, second_link]}

        # We would prefer the second link.
        eq_(second_link,
            AuthDoc._extract_link(
                links, 'alternate', prefer_type="text/plain"
            )
        )

        # We would prefer the first link.
        eq_(first_link,
            AuthDoc._extract_link(
                links, 'alternate', prefer_type="text/html"
            )
        )
        
        # The type we prefer is not available, so we get the first link.
        eq_(first_link,
            AuthDoc._extract_link(
                links, 'alternate', prefer_type="application/xhtml+xml"
            )
        )

    def test_empty_document(self):
        """Provide an empty Authentication For OPDS document to test
        default values.
        """
        place = MockPlace()
        everywhere = place.everywhere(None)
        parsed = AuthDoc.from_string(None, "{}", place)        
        
        # In the absence of specific information, it's assumed the
        # OPDS server is open to everyone.
        eq_(everywhere, parsed.service_area)
        eq_(everywhere, parsed.focus_area)
        eq_([parsed.PUBLIC_AUDIENCE], parsed.audiences)

        eq_(None, parsed.id)
        eq_(None, parsed.title)
        eq_(None, parsed.service_description)
        eq_(None, parsed.color_scheme)
        eq_(None, parsed.collection_size)
        eq_(None, parsed.public_key)
        eq_(None, parsed.website)
        eq_(None, parsed.registration)
        eq_(None, parsed.root)
        eq_({}, parsed.links)
        eq_(None, parsed.logo)
        eq_(None, parsed.logo_link)
        eq_(False, parsed.anonymous_access)

    def test_minimal_document(self):
        """Test a real, albeit minimal, Authentication For OPDS document."""
        document = """{"title": "Ansonia Public Library", "links": {"logo": {"href": "data:image/png;base64,some-image-data", "type": "image/png"}, "alternate": {"href": "http://ansonialibrary.org", "type": "text/html"}}, "providers": {"http://librarysimplified.org/terms/auth/library-barcode": {"methods": {"http://opds-spec.org/auth/basic": {"inputs": {"login": {"keyboard": "Default"}, "password": {"keyboard": "Default"}}, "labels": {"login": "Barcode", "password": "PIN"}}}, "name": "Library Barcode"}}, "service_description": "Serving Ansonia, CT", "color_scheme": "gold", "id": "c90903e0-d438-4c8d-ac35-94824d769e2c", "features": {"disabled": [], "enabled": ["https://librarysimplified.org/rel/policy/reservations"]}}"""
        place = MockPlace()
        everywhere = place.everywhere(None)
        parsed = AuthDoc.from_string(None, document, place)
        
        # Basic information about the OPDS server has been put into
        # the AuthenticationDocument object.
        eq_("c90903e0-d438-4c8d-ac35-94824d769e2c", parsed.id)
        eq_("Ansonia Public Library", parsed.title)
        eq_("Serving Ansonia, CT", parsed.service_description)
        eq_("gold", parsed.color_scheme)
        # collection size
        # public key
        eq_({u'href': u'http://ansonialibrary.org', u'type': u'text/html'},
            parsed.website)
        # registration
        # root
        # links
        eq_("data:image/png;base64,some-image-data", parsed.logo)
        eq_(None, parsed.logo_link)
        # anonymous access
