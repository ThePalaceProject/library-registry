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

