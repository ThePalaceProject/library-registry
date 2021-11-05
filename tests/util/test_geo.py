import pytest

from library_registry.util.geo import (
    InvalidLocationException,
    Location,
    LATLONG_STRING_REGEX,
    LATLONG_WKT_EWKT_REGEX,
)


class TestUtilGeoRegex:
    @pytest.mark.parametrize(
        "input_string,matches",
        [
            pytest.param("0, 0", ("0", "0"), id="zero_ints"),
            pytest.param("0.0, 0.0", ("0.0", "0.0"), id="zero_floats"),
            pytest.param("5.1, 5.2", ("5.1", "5.2"), id="single_digit_int_part"),
            pytest.param("11.1, 11.2", ("11.1", "11.2"), id="double_digit_int_part"),
            pytest.param("89, 179", ("89", "179"), id="int_both_values"),
            pytest.param("89, 179.1", ("89", "179.1"), id="int_first_value"),
            pytest.param("89.1, 179", ("89.1", "179"), id="int_second_value"),
            pytest.param("89.1, 179.1", ("89.1", "179.1"), id="float_both_values"),
            pytest.param("89.123456, 179.123456", ("89.123456", "179.123456"), id="six_digits_precision"),
            pytest.param("89.123456,179.123456", ("89.123456", "179.123456"), id="no_space_separator"),
            pytest.param("89.123456 179.123456", ("89.123456", "179.123456"), id="no_comma_separator"),
            pytest.param("89.123456,    179.123456", ("89.123456", "179.123456"), id="multi_space_separator"),
            pytest.param("89.123456    179.123456", ("89.123456", "179.123456"), id="multi_space_no_comma_separator"),
            pytest.param("91.12345, 181.12345", None, id="values_out_of_range"),
        ]
    )
    def test_latlong_string_regex(self, input_string, matches):
        """
        GIVEN: An input string
        WHEN:  The LATLONG_STRING_REGEX.match() is called on that string
        THEN:  Latitude and longitude values should be extracted if:
                    * The latitude appears at the start of the string
                    * The latitude is composed of an integer part from 0 to 90, and an optional decimal part
                    * The two numbers are separated by at least one space or comma, followed by zero or more spaces
                    * The longitude is composed of an integer part from 0 to 180, and an optional decimal part
                    * The longitude appears at the end of the string
        """
        match_obj = LATLONG_STRING_REGEX.match(input_string)

        if matches is None:
            assert match_obj is None
        else:
            assert match_obj.group('latitude') == matches[0]
            assert match_obj.group('longitude') == matches[1]

    @pytest.mark.parametrize(
        "input_string,matches",
        [
            pytest.param("POINT(0 0)", (None, "0", "0"), id="wkt_zero_ints"),
            pytest.param("POINT(0.0 0.0)", (None, "0.0", "0.0"), id="wkt_zero_floats"),
            pytest.param("POINT(5.2 5.1)", (None, "5.1", "5.2"), id="wkt_single_digit_int_part"),
            pytest.param("POINT(11.2 11.1)", (None, "11.1", "11.2"), id="wkt_double_digit_int_part"),
            pytest.param("POINT(179 89)", (None, "89", "179"), id="wkt_int_both_values"),
            pytest.param("POINT(179 89.1)", (None, "89.1", "179"), id="wkt_int_first_value"),
            pytest.param("POINT(179.1 89)", (None, "89", "179.1"), id="wkt_int_second_value"),
            pytest.param("POINT(179.1 89.1)", (None, "89.1", "179.1"), id="wkt_float_both_values"),
            pytest.param("POINT(179.123456 89.123456)", (None, "89.123456", "179.123456"), id="six_digits_precision"),
            pytest.param("POINT(181.12345 91.12345)", None, id="wkt_values_out_of_range"),
            pytest.param("SRID=4326;POINT(0 0)", ("4326", "0", "0"), id="ewkt_zero_ints"),
            pytest.param("SRID=3857;POINT(170.144 20.03)", ("3857", "20.03", "170.144"), id="ewkt_zero_ints"),
        ]
    )
    def test_latlong_wkt_ewkt_regex(self, input_string, matches):
        """
        GIVEN: An input string that may contain a WKT or EWKT Point
        WHEN:  LATLONG_WKT_EKT_REGEX.match() is called on that string
        THEN:  Latitude, and Longitude values should be extracted if:
                    * They appear inside a 'POINT()' enclosure, longitude first, space separated
                    * The longitude is composed of an integer part from 0 to 180, and an optional decimal part
                    * The latitude is composed of an integer part from 0 to 90, and an optional decimal part
               SRID should optionally be extracted if:
                    * The string begins with SRID=
                    * The following characters are integers, terminated by a semi-colon
        """
        match_obj = LATLONG_WKT_EWKT_REGEX.match(input_string)

        if matches is None:
            assert match_obj is None
        else:
            assert match_obj.groupdict()['srid'] == matches[0]
            assert match_obj.group('latitude') == matches[1]
            assert match_obj.group('longitude') == matches[2]


class TestLocation:
    def test_instantiate_success(self):
        """
        GIVEN: A valid representation of a location
        WHEN:  A Location object is instantiated based on that location
        THEN:  A valid Location object should be created
        """
        latitude = 40.75238
        longitude = -73.98018
        srid = 4326
        wkt = f"POINT({longitude} {latitude})"
        ewkt = f"SRID={srid};{wkt}"

        location_obj = Location(ewkt)

        assert location_obj.latitude == latitude
        assert location_obj.longitude == longitude
        assert location_obj.srid == srid
        assert location_obj.in_ocean is False
        assert location_obj.wkt == wkt
        assert location_obj.ewkt == ewkt

    def test_instantiate_failure(self):
        """
        GIVEN: An invalid representation of a location
        WHEN:  A Location object is instanted based on that location
        THEN:  InvalidLocationException should be raised
        """
        with pytest.raises(InvalidLocationException):
            Location((95.5, 100.5, 3857))   # Invalid latitude

        with pytest.raises(InvalidLocationException):
            Location((89.5, 195.5))         # Invalid longitude

    @pytest.mark.parametrize(
        "location_one,location_two,result",
        [
            pytest.param(
                'POINT(-129.000001 38.000001)', 'POINT(-129.000002 38.000002)', False, id="unequal_last_digit"
            ),
            pytest.param(
                'POINT(-129.000001 38.000001)', 'POINT(-129.000001 38.000001)', True, id="equal_to_six_digits"
            ),
            pytest.param(
                'POINT(-129 38)', 'POINT(-129.0 38.0)', True, id="integer_input"
            ),
            pytest.param(
                'POINT(-129.0000036 38.0000036)', 'POINT(-129.0000039 38.0000039)', True, id="rounding_seven_digits"
            )
        ]
    )
    def test_equality(self, location_one, location_two, result):
        """
        GIVEN: Two objects, at least one of which is a Location
        WHEN:  They are compared using the == equality operator
        THEN:  The boolean value returned by the comparison should be True if:
                * Both objects are Location instances
                * The latitude and longitude of the instances are the same
                  when rounded to six digits of precision.
        """
        assert bool(Location(location_one) == Location(location_two)) is result

    @pytest.mark.parametrize(
        "location,result",
        [
            pytest.param((34.03, -139.64), True, id="pacific_ocean_1"),
            pytest.param((13.51, -109.45), True, id="pacific_ocean_2"),
            pytest.param((18.35, -179.28), True, id="pacific_ocean_3"),
            pytest.param((17.69, 170.09), True, id="pacific_ocean_4"),
            pytest.param((-49.77, 122.08), True, id="pacific_ocean_5"),
            pytest.param((17.44, 137.49), True, id="phillipine_sea"),
            pytest.param((12.99, 87.47), True, id="bay_of_bengal"),
            pytest.param((-17.60, 80.80), True, id="indian_ocean"),
            pytest.param((12.13, 65.11), True, id="arabian_sea"),
            pytest.param((-65.33, -162.55), True, id="southern_ocean"),
            pytest.param((-21.14, -13.30), True, id="south_atlantic"),
            pytest.param((52.94, -30.55), True, id="north_atlantic_1"),
            pytest.param((23.32, -41.26), True, id="north_atlantic_2"),
            pytest.param((25.20, -88.55), True, id="gulf_of_mexico"),
            pytest.param((40.75238, -73.98018), False, id="nyc"),
            pytest.param((32.94, -96.82), False, id="texas"),
        ]
    )
    def test_latlong_in_ocean(self, location, result):
        """
        GIVEN: A valid, well-formed location (as per normalize_point_input)
        WHEN:  Location.location_in_ocean() is called on those values
        THEN:  A boolean value is returned, representing a guess about whether the point
               those coordinates describe is in the middle of the ocean.
        """
        assert Location.location_in_ocean(location) is result

    @pytest.mark.parametrize(
        "input_point,result",
        [
            pytest.param((33.33, 105.5), (33.33, 105.5, None), id="valid_two_tuple"),
            pytest.param(("a", "b"), (None, None, None), id="invalid_two_tuple"),
            pytest.param((95.5, 105.5, 3857), (None, None, None), id="tuple_invalid_latitude"),
            pytest.param((85.5, 185.5, 3857), (None, None, None), id="tuple_invalid_longitude"),
            pytest.param((85.5, 175.5, 3857.5), (85.5, 175.5, None), id="tuple_invalid_srid_float"),
            pytest.param((85.5, 175.5, "3857.5"), (85.5, 175.5, None), id="tuple_invalid_srid_string"),
            pytest.param(("33.33", "b"), (None, None, None), id="invalid_two_tuple"),
            pytest.param((33.33, 105.5, 3857), (33.33, 105.5, 3857), id="valid_three_tuple"),
            pytest.param(("a", "b", "c"), (None, None, None), id="invalid_three_tuple"),
            pytest.param((33.33, 105.5, 3857), (33.33, 105.5, 3857), id="valid_three_tuple"),
            pytest.param("33.33, 105.5", (33.33, 105.5, None), id="comma_separated_string"),
            pytest.param("33.33 105.5", (33.33, 105.5, None), id="space_separated_string"),
            pytest.param("POINT(102.11 82.3)", (82.3, 102.11, None), id="wkt_string"),
            pytest.param("SRID=4326;POINT(102.11 82.3)", (82.3, 102.11, 4326), id="ewkt_string"),
            pytest.param("not a real value", (None, None, None), id="bad_string_input"),
            pytest.param("SRID=4326.0;POINT(102.11 82.3)", (None, None, None), id="ewkt_invalid_srid"),
            pytest.param("SRID=4326;POINT(100.1 101.2)", (None, None, None), id="ewkt_invalid_latitude"),
            pytest.param("SRID=4326;POINT(200.1, 89.1)", (None, None, None), id="ewkt_invalid_longitude"),
        ]
    )
    def test_normalize_location_input(self, input_point, result):
        """
        GIVEN: A representation of a latitude/longitude point
        WHEN:  Location.normalize_location_input() is called on that representation
        THEN:  A 3-tuple of (latitude, longitude, srid) should be returned
        """
        (latitude, longitude, srid) = Location.normalize_location_input(input_point)
        assert (latitude, longitude, srid) == result

        if latitude:
            assert isinstance(latitude, float)

        if longitude:
            assert isinstance(longitude, float)

        if srid:
            assert isinstance(srid, int)
