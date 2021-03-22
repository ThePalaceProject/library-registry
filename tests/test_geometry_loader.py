from io import StringIO

import pytest

from library_registry.geometry_loader import GeometryLoader
from library_registry.model import Place, PlaceAlias, get_one_or_create
from sqlalchemy import func


@pytest.fixture
def geoloader(db_session):
    return GeometryLoader(db_session)


@pytest.fixture
def zip_77977(geoloader):
    metadata = (
        '{"parent_id": null, "name": "77977", "id": "77977", "type": "postal_code", '
        '"aliases": [{"name": "The 977", "language": "eng"}]}'
    )
    geography = (
        '{"type": "Polygon", "coordinates": [[[-96.840066, 28.683039], [-96.830637, 28.690131], '
        '[-96.835048, 28.693599], [-96.833515, 28.694926], [-96.82657, 28.699584], [-96.822495, 28.695826], '
        '[-96.821248, 28.696391], [-96.814249, 28.700983], [-96.772337, 28.722765], [-96.768804, 28.725363], '
        '[-96.768564, 28.725046], [-96.767246, 28.723276], [-96.765295, 28.722084], [-96.764568, 28.720456], '
        '[-96.76254, 28.718483], [-96.763087, 28.717521], [-96.761814, 28.716488], [-96.761088, 28.713623], '
        '[-96.762231, 28.712798], [-96.75967, 28.709812], [-96.781093, 28.677548], [-96.784803, 28.675363], '
        '[-96.793788, 28.669546], [-96.791527, 28.667603], [-96.808567, 28.678507], [-96.81505, 28.682946], '
        '[-96.820191, 28.684517], [-96.827178, 28.679867], [-96.828626, 28.681719], [-96.831309, 28.680451], '
        '[-96.83565, 28.677724], [-96.840066, 28.683039]]]}'
    )
    return geoloader.load(metadata, geography)


@pytest.fixture
def new_york_as_point(geoloader):
    metadata = (
        '{"parent_id": null, "name": "New York", "type": "state", "abbreviated_name": "NY", "id": "NY", '
        '"full_name": "New York", "aliases": [{"name": "New York State", "language": "eng"}]}'
    )
    geography = '{"type": "Point", "coordinates": [-75, 43]}'
    return geoloader.load(metadata, geography)


class TestGeometryLoader:
    """Test the GeometryLoader class"""

    def test_load_polygon(self, zip_77977):
        """
        GIVEN: 
            * A JSON 'metadata' object with the following keys:
                * parent_id - (string|null)
                * name - string
                * id - string
                * type - string
                * aliases - array of objects with keys 'name' and 'language'
            * A JSON 'geography' object with keys 'type' and 'coordinates', representing a Polygon, with a nested
                list of lat/long coordinates
        WHEN:  GeometryLoader.load(metadata, geography) is called
        THEN:  A geometry object is returned, with appropriate metadata and shape
        """
        (texas_zip, is_new) = zip_77977
        assert is_new is True
        assert texas_zip.external_id == "77977"
        assert texas_zip.external_name == "77977"
        assert texas_zip.parent is None
        assert texas_zip.type == "postal_code"

        [alias] = texas_zip.aliases
        assert alias.name == "The 977"
        assert alias.language == "eng"

    def test_load_point(self, new_york_as_point):
        """Load another place identified by a GeoJSON Point"""
        (new_york, is_new) = new_york_as_point
        assert is_new is True
        assert new_york.abbreviated_name == "NY"
        assert new_york.external_name == "New York"

        [alias] = new_york.aliases
        assert alias.name == "New York State"
        assert alias.language == "eng"

    def test_distances_of_loaded_objects(self, db_session, zip_77977, new_york_as_point):
        """We can measure the distance in kilometers between New York and Texas"""
        (texas_zip, _) = zip_77977
        (new_york, _) = new_york_as_point

        distance_func = func.ST_DistanceSphere(new_york.geometry, texas_zip.geometry)
        distance_qu = db_session.query().add_columns(distance_func)
        [[distance]] = distance_qu.all()
        assert int(distance/1000) == 2510

    def test_update_on_reload(self, db_session, geoloader, zip_77977, new_york_as_point):
        """If we load the same place again, but with a different geography, the Place object is updated"""
        metadata = (
            '{"parent_id": null, "name": "New York", "type": "state", "abbreviated_name": "NY", "id": "NY", '
            '"full_name": "New York", "aliases": [{"name": "New York State", "language": "eng"}]}'
        )
        geography = '{"type": "Point", "coordinates": [-74, 44]}'
        (new_york, _) = new_york_as_point
        (new_york_2, is_new) = geoloader.load(metadata, geography)
        assert is_new is False
        assert new_york == new_york_2

        # This changes the distance between the two points.
        (texas_zip, _) = zip_77977
        distance_func = func.ST_DistanceSphere(new_york_2.geometry, texas_zip.geometry)
        distance_qu = db_session.query().add_columns(distance_func)
        [[distance]] = distance_qu.all()
        assert int(distance/1000) == 2637

    def test_load_ndjson(self, shared_datadir, db_session, geoloader):
        """
        Test GeometryLoader.load_ndjson()
        """
        # Create a preexisting Place with an alias.
        (old_us, is_new) = get_one_or_create(
            db_session, Place, parent=None, external_name="United States",
            external_id="US", type="nation", geometry='SRID=4326;POINT(-75 43)'
        )
        assert old_us.abbreviated_name is None
        old_alias = get_one_or_create(db_session, PlaceAlias, name="USA", language="eng", place=old_us)
        old_us_geography = old_us.geometry

        # Load a small NDJSON "file" containing information about three places.
        test_ndjson = (shared_datadir / "test_geometry_loader_ndjson.ndjson").read_text()

        input = StringIO(test_ndjson)
        [(us, ignore), (alabama, ignore), (montgomery, ignore)] = list(
            geoloader.load_ndjson(input)
        )

        # All three places were loaded as Place objects and their
        # relationships to each other were maintained.
        assert isinstance(us, Place)
        assert isinstance(alabama, Place)
        assert isinstance(montgomery, Place)
        assert us.parent is None
        assert alabama.parent == us
        assert montgomery.parent == alabama

        # The place that existed before we ran the loader is still the
        # same database object, but it has had additional information
        # associated with it.
        assert us == old_us
        assert us.abbreviated_name == "US"

        # And its geography has been updated.
        assert old_us_geography != us.geometry

        # Its preexisting alias has been preserved, and a new alias added.
        [new_alias, old_alias] = sorted(us.aliases, key=lambda x: x.name)
        assert old_alias.name == "USA"

        assert new_alias.name == "The Good Old U. S. of A."
        assert new_alias.language == "eng"

        # We can measure the distance in kilometers between the point chosen to represent 'Montgomery'
        # and the point chosen to represent 'Alabama'.
        distance_func = func.ST_DistanceSphere(montgomery.geometry, alabama.geometry)
        [[distance]] = db_session.query().add_columns(distance_func).all()
        assert int(distance/1000) == 276
