from nose.tools import set_trace
from sqlalchemy import func
from sqlalchemy.sql.expression import cast
from geoalchemy2 import Geometry

class GeometryUtility(object):

    @classmethod
    def from_geojson(cls, geojson):
        """Turn a GeoJSON string into a Geometry object that can
        be put into the database.
        """
        geometry = func.ST_GeomFromGeoJSON(geojson)
        geometry = func.ST_SetSRID(geometry, 4326)
        return cast(geometry, Geometry)
