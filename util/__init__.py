from geolite2 import geolite2
from sqlalchemy import func


class GeometryUtility:
    @classmethod
    def from_geojson(cls, geojson):
        """
        Turn a GeoJSON string into a Geometry object that can be put into the database

        :param geojson: (str) - Valid GeoJSON object, suitable for loading by the PostGIS
            function ST_GeomFromGeoJSON() (https://postgis.net/docs/ST_GeomFromGeoJSON.html)
        :return:
        """
        geometry = func.ST_GeomFromGeoJSON(geojson)
        geometry = func.ST_SetSRID(geometry, 4326)
        return geometry

    @classmethod
    def point_from_ip(cls, ip_address):
        """
        For a given IPv4 string, query the MaxMind GeoIP database

        :param ip_address: (str) - IPv4, dot-separated quad
        :return: (str, None) - None if no match found, otherwise a string representing
            a single point with latitude and longitude, in the format

                'SRID=4326;POINT({longitude} {latitude})'
        """
        reader = geolite2.reader()  # TODO: Move this instantiation after None check
        if not ip_address:
            return None

        match = reader.get(ip_address)
        if match is None:
            return None

        latitude, longitude = [match["location"][x] for x in ("latitude", "longitude")]
        return cls.point(latitude, longitude)

    @classmethod
    def point_from_string(cls, s):
        """
        Parse a string representing latitude and longitude into a Geometry object

        :param s: (str) - Comma separated lat/long pair
        :return: (str) -
        """
        if not s or "," not in s:
            return None

        parts = []
        for i in s.split(",", 1):
            try:
                i = float(i.strip())
            except ValueError:
                return None
            parts.append(i)

        if any(abs(x) > 180 for x in parts):
            return None

        return cls.point(*parts)

    @classmethod
    def point(cls, latitude, longitude):
        """
        Convert latitude/longitude to a string that can be used as a Geometry

        :param latitude:
        :param longitude:
        :return: (str) - Formatted string: 'SRID=4326;POINT({longitude} {latitude})'
        """
        return "SRID=4326;POINT(%s %s)" % (longitude, latitude)
