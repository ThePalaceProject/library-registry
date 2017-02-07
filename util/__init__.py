from nose.tools import set_trace
from sqlalchemy import func
from geoalchemy2 import Geometry
from geoip import geolite2

class GeometryUtility(object):

    @classmethod
    def from_geojson(cls, geojson):
        """Turn a GeoJSON string into a Geometry object that can
        be put into the database.
        """
        geometry = func.ST_GeomFromGeoJSON(geojson)
        geometry = func.ST_SetSRID(geometry, 4326)
        return geometry

    @classmethod
    def point_from_ip(cls, ip_address):
        match = geolite2.lookup(ip_address)
        if match is None:
            return None
        return cls.point(*match.location)        
    
    @classmethod
    def point(cls, latitude, longitude):
        """Convert latitude/longitude to a string that can be
        used as a Geometry.
        """
        return 'SRID=4326;POINT (%s %s)' % (longitude, latitude)
