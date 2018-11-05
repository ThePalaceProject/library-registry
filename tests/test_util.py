from nose.tools import (
    set_trace,
    eq_,
)
from util import GeometryUtility

class TestGeometryUtility(object):

    def test_point(self):
        point = GeometryUtility.point("80", "-4")
        eq_('SRID=4326;POINT (-4 80)', point)

    def test_point_from_ip(self):
        point = GeometryUtility.point_from_ip("65.88.88.124")
        eq_('SRID=4326;POINT (-73.9813 40.7769)', point)

    def test_point_from_string(self):
        m = GeometryUtility.point_from_string

        # Lots of strings don't map to latitude/longitude.
        eq_(None, m(None))
        eq_(None, m("No comma"))
        eq_(None, m("Not a number, -71"))
        eq_(None, m("-400,1"))
        eq_(None, m("1,400"))

        # Here are some strings that do.
        for coords in ("40.7769, -73.9813", "40.7769,-73.9813"):
            eq_('SRID=4326;POINT (-73.9813 40.7769)', m(coords))

