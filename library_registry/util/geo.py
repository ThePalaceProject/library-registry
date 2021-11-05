import re


# Regex for extracting latitude and longitude from a string like "10.1234, 110.1234"
LATLONG_STRING_REGEX = re.compile(r"""^                     # Start of string
                                      (?P<latitude>         # latitude, capturing
                                        -?                  # Optional leading negative sign
                                        (?:                 # Lat. before decimal, non-capturing
                                            90          |   # Double digit 90
                                            [1-8][0-9]  |   # Double digit 10-89
                                            [0-9]           # Single digit 0-9
                                        )
                                        (?:                 # Latitude decimal portion, non-capturing
                                            \.              # Decimal separator
                                            [0-9]{1,}       # One or more digits of precision
                                        )?                  # Precision digits/decimal are optional
                                      )
                                      [, ]{1}               # A single comma or space, to separate lat/long
                                      [ ]{0,}               # Optional additional spaces
                                      (?P<longitude>
                                        -?                  # Optional leading negative sign
                                        (?:
                                            180         |   # triple digit 180
                                            1[0-7][0-9] |   # triple digit 100-179
                                            [1-9][0-9]  |   # double digit 10-99
                                            [0-9]           # single digit 0-9
                                        )
                                        (?:                 # Longitude decimal portion, non-capturing
                                            \.              # Decimal separator
                                            [0-9]{1,}       # One or more digits of precision
                                        )?                  # Precision digits/decimal are optional
                                       )
                                       """, re.VERBOSE)

# Regex for extracting srid, latitude, and longitude from:
#   * A Well-Known Text string like "POINT(110.1234 10.1234)"
#   * An Extended Well-Known Text string like "SRID=1234;POINT(110.1234 10.1234)"
LATLONG_WKT_EWKT_REGEX = re.compile(r"""^
                                        (?:
                                            SRID=(?P<srid>[0-9]{1,});   # If this is EWKT, starts with SRID
                                        )?                              # However, this is entirely optional
                                        POINT\(
                                            (?P<longitude>
                                                -?(?: 180 | 1[0-7][0-9] | [1-9][0-9] | [0-9])
                                                  (?: \. [0-9]{1,})?
                                            )
                                            [ ]{1}
                                            (?P<latitude>
                                                -?(?: 90 | [1-8][0-9] | [0-9])
                                                  (?: \. [0-9]{1,})?
                                            )
                                        \)$
                                     """, flags=re.VERBOSE | re.IGNORECASE)


class InvalidLocationException(Exception):
    """Raised when a Location is created with invalid input"""


class Location:
    """A Location represents a point on the earth."""
    def __init__(self, location):
        (self.latitude, self.longitude, self.srid) = self.normalize_location_input(location)

        if not (self.latitude and self.longitude):
            raise InvalidLocationException(f"Could not create a Location from input: {location}")

        if not self.srid:
            self.srid = 4326

        self.in_ocean = self.location_in_ocean(location)
        self.wkt = f"POINT({self.longitude} {self.latitude})"
        self.ewkt = f"SRID={self.srid};{self.wkt}"

    def __str__(self):
        return self.ewkt

    def __repr__(self):
        return f"<Location: latitude={self.latitude}, longitude={self.longitude}, srid={self.srid}>"

    def __eq__(self, other):
        """
        Locations are considered equal for our purposes if their latitude and longitude
        match to 6 digits of precision.
        """
        if not isinstance(other, Location):
            return False

        if (
            round(self.latitude, 6) == round(other.latitude, 6) and
            round(self.longitude, 6) == round(other.longitude, 6)
        ):
            return True
        else:
            return False

    @classmethod
    def normalize_location_input(cls, location):
        """
        Convert any of several formats to a 3-tuple of (latitude, longitude, srid), where
            * latitude and longitude are floats
            * srid is an integer or None

        location may be any one of:

            - A 2-tuple of (latitude, longitude)
            - A 3-tuple of (latitude, longitude, srid)
            - A comma and/or space separated string with 'latitude, longitude'
            - A Well Known Text string of type Point, such as 'POINT(longitude latitude)'
            - An Extended WKT string of type Point, such as 'SRID=4326;POINT(longitude latitude)'
        """
        (latitude, longitude, srid) = (None, None, None)

        if isinstance(location, tuple):
            if len(location) == 2:
                (latitude, longitude) = location
            elif len(location) == 3:
                (latitude, longitude, srid) = location
        elif isinstance(location, str):
            if ',' in location or (' ' in location and not location.upper().startswith(('POINT', 'SRID'))):
                # If it's got a comma, or doesn't start with POINT or SRID, it's not a WKT/EWKT Point string
                match = LATLONG_STRING_REGEX.match(location)
                if match:
                    latitude = match.group('latitude')
                    longitude = match.group('longitude')
            elif location.upper().startswith(('POINT', 'SRID')):  # it's a WKT/EWKT string
                match = LATLONG_WKT_EWKT_REGEX.match(location)
                if match:
                    srid = match.groupdict()['srid']
                    latitude = match.group('latitude')
                    longitude = match.group('longitude')

        # Perform type coercion on the values we got
        try:
            latitude = float(latitude)
            assert abs(latitude) <= 90.0
            longitude = float(longitude)
            assert abs(longitude) <= 180.0
        except (TypeError, ValueError, AssertionError):
            (latitude, longitude) = (None, None)  # These live or die together. No sense returning only one.

        try:
            assert (latitude and longitude)       # If lat/long didn't pass, srid doesn't matter
            assert str(srid) == str(int(srid))    # srid has to be a real int, even if it's in a string
            srid = int(srid)
        except (TypeError, ValueError, AssertionError):
            srid = None

        # At this point either we've got real values from the tuple or string, or the location
        # passed in wasn't a string or a tuple, so we can safely ignore it and return nothing.
        return (latitude, longitude, srid)

    @classmethod
    def location_in_ocean(cls, location):
        """
        Roughly confirm that a location is in the ocean.

        Checks to make sure the values are in bounds for their type, then does a very
        rough check for whether they're in some very big boxes in the middle of the ocean.
        """
        (latitude, longitude, _) = cls.normalize_location_input(location)  # We don't really care about the SRID

        if not (latitude and longitude):
            return False

        ocean_boxes = [
            {"lat": (-12.35,  50.25), "lon": (-151.27, -129.95)},  # Pacific 1, CA to HI                # noqa: E201
            {"lat": (-24.44,  14.62), "lon": (-146.73,  -94.48)},  # Pacific 2, south of Mexico         # noqa: E201
            {"lat": ( -9.35,  50.98), "lon": (-180.0,  -162.44)},  # Pacific 3: west of HI              # noqa: E201
            {"lat": ( -1.48,  41.61), "lon": ( 145.66,  180.00)},  # Pacific 4: West of Int. Dateline   # noqa: E201
            {"lat": (-64.47, -35.54), "lon": (  73.84,  135.41)},  # Pacific 5: Australia to Antarctica # noqa: E201
            {"lat": (  2.33,  27.57), "lon": ( 129.39,  145.66)},  # Phillipine Sea                     # noqa: E201
            {"lat": ( -7.83,  16.89), "lon": (  82.89,   94.09)},  # Bay of Bengal                      # noqa: E201
            {"lat": (-47.83,   5.45), "lon": (  51.07,   94.53)},  # Indian Ocean                       # noqa: E201
            {"lat": (-18.17,  14.22), "lon": (  55.71,   72.32)},  # Arabian Sea                        # noqa: E201
            {"lat": (-70.54, -18.42), "lon": (-180.0,  -116.75)},  # Southern Ocean                     # noqa: E201
            {"lat": (-66.11,   2.22), "lon": ( -33.89,    7.24)},  # South Atlantic                     # noqa: E201
            {"lat": ( 40.87,  56.60), "lon": ( -51.67,  -10.17)},  # North Atlantic 1, Canada to UK     # noqa: E201
            {"lat": ( 18.69,  42.81), "lon": ( -65.21,  -18.94)},  # North Atlantic 2, PR to W. Africa  # noqa: E201
            {"lat": ( 22.78,  28.69), "lon": ( -96.44,  -84.44)},  # Gulf of Mexico                     # noqa: E201
        ]

        for bounds in ocean_boxes:
            (lat, lon) = (bounds["lat"], bounds["lon"])
            if (lat[0] < latitude < lat[1]) and (lon[0] < longitude < lon[1]):
                return True     # The point the inputs describe is in the middle of the ocean.

        return False
