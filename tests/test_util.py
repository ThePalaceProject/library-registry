import pytest

from library_registry.util import GeometryUtility


class TestGeometryUtility():
    def test_from_geojson(self, shared_datadir, capsys):
        crude_us_geojson = crude_us_geojson = (shared_datadir / 'crude_us_geojson.json').read_text()
        geometry_obj = GeometryUtility.from_geojson(crude_us_geojson)
        from sqlalchemy.sql.functions import Function
        assert isinstance(geometry_obj, Function)
        # TODO: Expand this test

    @pytest.mark.parametrize(
        "ip,expected",
        [
            ("65.88.88.124", "SRID=4326;POINT (-73.9169 40.8056)"),
            ("127.0.0.1", None)
        ]
    )
    def test_point_from_ip(self, ip, expected):
        if expected is None:
            assert GeometryUtility.point_from_ip(ip) is None
        else:
            assert GeometryUtility.point_from_ip(ip) == expected
        
    @pytest.mark.parametrize(
        "bad_point_from_string_value",
        [(None), ("No comma"), ("Not a number, -71"), ("-400,1"), ("1,400")]
    )
    def test_point_from_string_bad_input(self, bad_point_from_string_value):
        assert GeometryUtility.point_from_string(bad_point_from_string_value) is None

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("40.7769, -73.9813", "SRID=4326;POINT (-73.9813 40.7769)"),
            ("40.7769,-73.9813", "SRID=4326;POINT (-73.9813 40.7769)"),
        ]
    )
    def test_point_from_string(self, input_val, expected):
        assert GeometryUtility.point_from_string(input_val) == expected

    def test_point(self):
        point = GeometryUtility.point("80", "-4")
        assert point == 'SRID=4326;POINT (-4 80)'
