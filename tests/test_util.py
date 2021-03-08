from library_registry.util import GeometryUtility


class TestGeometryUtility():
    def test_from_geojson(self, shared_datadir, capsys):
        crude_us_geojson = crude_us_geojson = (shared_datadir / 'crude_us_geojson.json').read_text()
        geometry_obj = GeometryUtility.from_geojson(crude_us_geojson)
        from sqlalchemy.sql.functions import Function
        assert isinstance(geometry_obj, Function)
        # TODO: Expand this test

    def test_point_from_ip(self):
        point = GeometryUtility.point_from_ip("65.88.88.124")
        assert point == 'SRID=4326;POINT (-73.9169 40.8056)'

        point = GeometryUtility.point_from_ip("127.0.0.1")
        assert point is None

    def test_point_from_string(self):
        m = GeometryUtility.point_from_string

        # Lots of strings don't map to latitude/longitude.
        assert m(None) is None
        assert m("No comma") is None
        assert m("Not a number, -71") is None
        assert m("-400,1") is None
        assert m("1,400") is None

        # Here are some strings that do.
        for coords in ("40.7769, -73.9813", "40.7769,-73.9813"):
            assert m(coords) == 'SRID=4326;POINT (-73.9813 40.7769)'

    def test_point(self):
        point = GeometryUtility.point("80", "-4")
        assert point == 'SRID=4326;POINT (-4 80)'
