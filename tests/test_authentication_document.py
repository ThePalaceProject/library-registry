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
    NoResultFound,
)
from authentication_document import AuthenticationDocument
from . import DatabaseTest
from model import (
    Place,
    ServiceArea,
)
from util.problem_detail import ProblemDetail
from problem_details import INVALID_AUTH_DOCUMENT

# Alias for a long class name
AuthDoc = AuthenticationDocument

class MockPlace(object):
    """Used to test AuthenticationDocument.parse_coverage."""

    # Used to indicate that a place name is ambiguous.
    AMBIGUOUS = object()

    # Used to indicate coverage through the universe or through a
    # country.
    EVERYWHERE = object()

    by_name = dict()

    def __init__(self, inside=None):
        self.inside = inside or dict()

    @classmethod
    def lookup_one_by_name(cls, _db, name, place_type):
        place = cls.by_name.get(name)
        if place is cls.AMBIGUOUS:
            raise MultipleResultsFound()
        if place is None:
            raise NoResultFound()
        return place
        
    def lookup_inside(self, name):
        place = self.inside.get(name)
        if place is self.AMBIGUOUS:
            raise MultipleResultsFound()
        if place is None:
            raise NoResultFound()
        return place

    @classmethod
    def everywhere(cls, _db):
        return cls.EVERYWHERE

class TestParseCoverage(object):

    EVERYWHERE = AuthenticationDocument.COVERAGE_EVERYWHERE
    
    def parse_places(self, coverage_object, expected_places=None,
                     expected_unknown=None, expected_ambiguous=None):
        """Call AuthenticationDocument.parse_coverage. Verify that the parsed
        list of places, as well as the dictionaries of unknown and
        ambiguous place names, are as expected.
        """
        place_objs, unknown, ambiguous = AuthDoc.parse_coverage(
            None, coverage_object, MockPlace
        )
        empty = defaultdict(list)
        expected_places = expected_places or []
        expected_unknown = expected_unknown or empty
        expected_ambiguous = expected_ambiguous or empty
        eq_(sorted(expected_places), sorted(place_objs))
        eq_(expected_unknown, unknown)
        eq_(expected_ambiguous, ambiguous)
        
    def test_universal_coverage(self):
        # Test an authentication document that says a library covers the
        # whole universe.
        self.parse_places(
            self.EVERYWHERE, [MockPlace.EVERYWHERE]
        )

    def test_entire_country(self):
        # Test an authentication document that says a library covers an
        # entire country.
        us = MockPlace()
        MockPlace.by_name["US"] = us
        self.parse_places(
            {"US": self.EVERYWHERE },
            expected_places=[us]
        )

    def test_ambiguous_country(self):
        # Test the unlikely scenario where an authentication document says a
        # library covers an entire country, but it's ambiguous which
        # country is being referred to.

        canada = MockPlace()
        MockPlace.by_name["CA"] = canada
        MockPlace.by_name["Europe I think?"] = MockPlace.AMBIGUOUS
        self.parse_places(
            {"Europe I think?": self.EVERYWHERE, "CA": self.EVERYWHERE },
            expected_places=[canada],
            expected_ambiguous={"Europe I think?": self.EVERYWHERE}
        )

    def test_unknown_country(self):
        # Test an authentication document that says a library covers an
        # entire country, but the library registry doesn't know anything about
        # that country's geography.

        canada = MockPlace()
        MockPlace.by_name["CA"] = canada
        self.parse_places(
            {"Memory Alpha": self.EVERYWHERE, "CA": self.EVERYWHERE },
            expected_places=[canada],
            expected_unknown={"Memory Alpha": self.EVERYWHERE}
        )

    def test_places_within_country(self):
        # Test an authentication document that says a library
        # covers one or more places within a country.

        # This authentication document covers two places called
        # "San Francisco" (one in the US and one in Mexico) as well as a
        # place called "Mexico City" in Mexico.
        #
        # Note that it's invalid to map a country name to a single
        # place name (it's supposed to always be a list), but our
        # parser can handle it.
        doc = {"US": "San Francisco", "MX": ["San Francisco", "Mexico City"]}

        place1 = MockPlace()
        place2 = MockPlace()
        place3 = MockPlace()
        place4 = MockPlace()
        us = MockPlace(inside={"San Francisco": place1, "San Jose": place2})
        mx = MockPlace(inside={"San Francisco": place3, "Mexico City": place4})
        MockPlace.by_name["US"] = us
        MockPlace.by_name["MX"] = mx

        # AuthenticationDocument.parse_coverage is able to turn those
        # three place names into place objects.
        self.parse_places(
            doc,
            expected_places=[place1, place3, place4]
        )

    def test_ambiguous_place_within_country(self):
        # Test an authentication document that names an ambiguous
        # place within a country.
        us = MockPlace(inside={"Springfield": MockPlace.AMBIGUOUS})
        MockPlace.by_name["US"] = us

        self.parse_places(
            {"US": ["Springfield"]},
            expected_ambiguous={"US": ["Springfield"]}
        )

    def test_unknown_place_within_country(self):
        # Test an authentication document that names an unknown
        # place within a country.
        sf = MockPlace()
        us = MockPlace(inside={"San Francisco": sf})
        MockPlace.by_name["US"] = us

        self.parse_places(
            {"US": "Nowheresville"},
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
        eq_(([everywhere], {}, {}), parsed.service_area)
        eq_(([everywhere], {}, {}), parsed.focus_area)
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

    def test_real_document(self):
        """Test an Authentication For OPDS document that demonstrates
        most of the features we're looking for.
        """
        document = {"id": "c90903e0-d438-4c8d-ac35-94824d769e2c",
 "title": "Ansonia Public Library", 
 "links": {
    "logo": {"href": "data:image/png;base64,some-image-data", "type": "image/png"}, 
    "alternate": {"href": "http://ansonialibrary.org", "type": "text/html"},
    "register": {"href": "http://example.com/get-a-card/", "type": "text/html"},
    "start": [
      {"href": "http://catalog.example.com/", "type": "text/html/"}, 
      {"href": "http://opds.example.com/", "type": "application/atom+xml;profile=opds-catalog"}
    ]
 },
    "service_description": "Serving Ansonia, CT",
    "color_scheme": "gold",
    "collection_size": {"eng": 100, "spa": 20},
    "public_key": "a public key",
    "features": {"disabled": [], "enabled": ["https://librarysimplified.org/rel/policy/reservations"]},
    "providers": {"http://librarysimplified.org/terms/auth/library-barcode": {"methods": {"http://opds-spec.org/auth/basic": {"inputs": {"login": {"keyboard": "Default"}, "password": {"keyboard": "Default"}}, "labels": {"login": "Barcode", "password": "PIN"}}}, "name": "Library Barcode"}}
}
        place = MockPlace()
        everywhere = place.everywhere(None)
        parsed = AuthDoc.from_dict(None, document, place)
        
        # Information about the OPDS server has been extracted from
        # JSON and put into the AuthenticationDocument object.
        eq_("c90903e0-d438-4c8d-ac35-94824d769e2c", parsed.id)
        eq_("Ansonia Public Library", parsed.title)
        eq_("Serving Ansonia, CT", parsed.service_description)
        eq_("gold", parsed.color_scheme)
        eq_({"eng": 100, "spa": 20}, parsed.collection_size)
        eq_("a public key", parsed.public_key)
        eq_({u'href': u'http://ansonialibrary.org', u'type': u'text/html'},
            parsed.website)
        eq_({"href": "http://example.com/get-a-card/", "type": "text/html"},
            parsed.registration)
        eq_({"href": "http://opds.example.com/", "type": "application/atom+xml;profile=opds-catalog"}, parsed.root)
        eq_("data:image/png;base64,some-image-data", parsed.logo)
        eq_(None, parsed.logo_link)
        eq_(False, parsed.anonymous_access)

    def test_name_treated_as_title(self):
        """Some invalid documents put the library name in 'name' instead of title.
        We can handle these documents.
        """
        document = dict(name="My library")
        auth = AuthDoc.from_dict(None, document, MockPlace())
        eq_("My library", auth.title)

    def test_logo_link(self):
        """You can link to your logo, instead of including it in the
        document.
        """
        document = {"links": {"logo": {"href": "http://logo.com/logo.jpg"}}}
        auth = AuthDoc.from_dict(None, document, MockPlace())
        eq_(None, auth.logo)
        eq_({"href": "http://logo.com/logo.jpg"}, auth.logo_link)

    def test_audiences(self):
        """You can specify the target audiences for your OPDS server."""
        document = {"audience": ["educational-secondary", "research"]}
        auth = AuthDoc.from_dict(None, document, MockPlace())
        eq_(["educational-secondary", "research"], auth.audiences)
        
    def test_anonymous_access(self):
        """You can signal that your OPDS server allows anonymous access by
        including it as an authentication type.
        """
        document = {"type": ["http://opds-spec.org/auth/basic", 
                             "https://librarysimplified.org/rel/auth/anonymous"]}
        auth = AuthDoc.from_dict(None, document, MockPlace())
        eq_(True, auth.anonymous_access)


class TestUpdateServiceAreas(DatabaseTest):

    def test_known_place_becomes_servicearea(self):
        """Test the helper method in a successful case."""
        library = self._library()
        
        # We identified two places, with no ambiguous or unknown
        # places.
        p1 = self._place()
        p2 = self._place()
        valid = [p1, p2]
        ambiguous = []
        unknown = []

        ids = []

        # This will use those places to create new ServiceAreas.
        problem = AuthenticationDocument._update_service_areas(
            library, [valid, unknown, ambiguous], ServiceArea.FOCUS,
            ids
        )
        eq_(None, problem)
        
        [a1, a2] = sorted(library.service_areas, key = lambda x: x.place_id)
        eq_(p1, a1.place)
        eq_(ServiceArea.FOCUS, a1.type)

        eq_(p2, a2.place)
        eq_(ServiceArea.FOCUS, a2.type)

        # The ServiceArea IDs were added to the `ids` list.
        eq_(set([a1.id, a2.id]), set(ids))

        
    def test_ambiguous_and_unknown_places_become_problemdetail(self):
        """Test the helper method in a case that ends in failure."""
        library = self._library()
        
        # We were able to identify one valid place.
        valid = [self._place()]

        # But we also found unknown and ambiguous places.
        ambiguous = ["Ambiguous"]
        unknown = ["Unknown 1", "Unknown 2"]

        ids = []
        problem = AuthenticationDocument._update_service_areas(
            library, [valid, unknown, ambiguous], ServiceArea.ELIGIBILITY,
            ids
        )

        # We got a ProblemDetail explaining the problem
        assert isinstance(problem, ProblemDetail)
        eq_(INVALID_AUTH_DOCUMENT.uri, problem.uri)
        eq_(
            'The following service area was unknown: ["Unknown 1", "Unknown 2"]. The following service area was ambiguous: ["Ambiguous"].',
            problem.detail
        )

        # No IDs were added to the list.
        eq_([], ids)

    def test_update_service_areas(self):

        # This Library has no ServiceAreas associated with it.
        library = self._library()

        country1 = self._place(abbreviated_name="C1", type=Place.NATION)
        country2 = self._place(abbreviated_name="C2", type=Place.NATION)

        everywhere = AuthenticationDocument.COVERAGE_EVERYWHERE
        doc_dict = dict(
            service_area=everywhere,
            focus_area = { country1.abbreviated_name : everywhere,
                           country2.abbreviated_name : everywhere }
        )
        doc = AuthenticationDocument.from_dict(self._db, doc_dict)
        problem = doc.update_service_areas(library)
        self._db.commit()
        eq_(None, problem)

        # Now this Library has three associated ServiceAreas.
        [a1, a2, a3] = sorted(
            [(x.type, x.place.abbreviated_name)
             for x in library.service_areas]
        )
        everywhere_place = Place.everywhere(self._db)

        # Anyone is eligible for access.
        eq_(('eligibility', everywhere_place.abbreviated_name), a1)

        # But people in two particular countries are the focus.
        eq_(('focus', country1.abbreviated_name), a2)
        eq_(('focus', country2.abbreviated_name), a3)

        # Remove one of the countries from the focus, add a new one,
        # and try again.
        country3 = self._place(abbreviated_name="C3", type=Place.NATION)
        doc_dict = dict(
            service_area=everywhere,
            focus_area = { country1.abbreviated_name : everywhere,
                           country3.abbreviated_name : everywhere }
        )
        doc = AuthenticationDocument.from_dict(self._db, doc_dict)
        doc.update_service_areas(library)
        self._db.commit()
        
        # The ServiceArea for country #2 has been removed.
        assert a2 not in library.service_areas
        assert not any(a.place == country2 for a in library.service_areas)
        
        [a1, a2, a3] = sorted(
            [(x.type, x.place.abbreviated_name)
             for x in library.service_areas]
        )
        eq_(('eligibility', everywhere_place.abbreviated_name), a1)
        eq_(('focus', country1.abbreviated_name), a2)
        eq_(('focus', country3.abbreviated_name), a3)

    def test_service_area_registered_as_focus_area_if_no_focus_area(self):

        library = self._library()
        # Create an authentication document that defines service_area
        # but not focus_area.
        everywhere = AuthenticationDocument.COVERAGE_EVERYWHERE
        doc_dict = dict(service_area=everywhere)
        doc = AuthenticationDocument.from_dict(self._db, doc_dict)
        problem = doc.update_service_areas(library)
        self._db.commit()
        eq_(None, problem)

        # We have a focus area but no explicit eligibility area. This
        # means that the library's eligibility area and focus area are
        # the same.
        [area] = library.service_areas
        eq_(Place.EVERYWHERE, area.place.type)
        eq_(ServiceArea.FOCUS, area.type)


    def test_service_area_registered_as_focus_area_if_identical_to_focus_area(self):
        library = self._library()

        # Create an authentication document that defines service_area
        # and focus_area as the same value.
        everywhere = AuthenticationDocument.COVERAGE_EVERYWHERE
        doc_dict = dict(
            service_area=everywhere,
            focus_area=everywhere,
        )
        doc = AuthenticationDocument.from_dict(self._db, doc_dict)
        problem = doc.update_service_areas(library)
        self._db.commit()
        eq_(None, problem)

        # Since focus area and eligibility area are the same, only the
        # focus area was registered.
        [area] = library.service_areas
        eq_(Place.EVERYWHERE, area.place.type)
        eq_(ServiceArea.FOCUS, area.type)
