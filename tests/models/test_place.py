from library_registry.config import Configuration
from library_registry.model import (
    ConfigurationSetting,
    Place,
)


class TestPlace:
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

    def test_relation_alias(self, places, capsys):
        """
        GIVEN: A Place object which was referenced in the creation of a PlaceAlias object
        WHEN:  The 'aliases' attribute of that Place object is examined
        THEN:  That attribute should contain a reference to the PlaceAlias object
        """
        nyc_aliases = places["new_york_city"].aliases
        assert "Manhattan" in [x.name for x in nyc_aliases]
        assert "Brooklyn" in [x.name for x in nyc_aliases]
        assert "New York" in [x.name for x in nyc_aliases]

    def test_default_nation_unset(self, db_session):
        """
        GIVEN: The sitewide setting DEFAULT_NATION_ABBREVIATION is unset
        WHEN:  Place.default_nation() is called on the test db
        THEN:  The Place.default_nation() method should return None
        """
        setting = ConfigurationSetting.sitewide(db_session, Configuration.DEFAULT_NATION_ABBREVIATION)
        assert setting.value is None
        assert Place.default_nation(db_session) is None

    def test_default_nation_set(self, db_session, places):
        """
        GIVEN: The sitewide setting DEFAULT_NATION_ABBREVIATION is explicitly set
        WHEN:  Place.default_nation() is called on the test db
        THEN:  The Place.default_nation() method should return the set value
        """
        setting = ConfigurationSetting.sitewide(db_session, Configuration.DEFAULT_NATION_ABBREVIATION)
        setting.value = places["crude_us"].abbreviated_name
        default_nation_place = Place.default_nation(db_session)
        assert isinstance(default_nation_place, Place)
        assert default_nation_place.abbreviated_name == places["crude_us"].abbreviated_name
