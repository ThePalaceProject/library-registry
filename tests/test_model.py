from nose.tools import (
    eq_,
    set_trace,
)
from sqlalchemy import func

from model import (
    get_one,
    get_one_or_create,
    Library,
    Place,
    PlaceAlias,
)

from . import (
    DatabaseTest,
)


class TestPlace(DatabaseTest):

    def test_creation(self):       
        # Create some US states represented by points.
        # (Rather than by multi-polygons, as they will be represented in
        # the actual application.)
        new_york, is_new = get_one_or_create(
            self._db, Place, type=Place.STATE, external_id='04',
            external_name='New York',
            create_method_kwargs=dict(geography='POINT(-75 43)')
        )
        eq_(True, is_new)
        
        new_mexico, is_new = get_one_or_create(
            self._db, Place, type=Place.STATE, external_id='21',
            external_name='New Mexico',
            create_method_kwargs=dict(geography='POINT(-106 34)')
        )
        
        connecticut, is_new = get_one_or_create(
            self._db, Place, type=Place.STATE, external_id='14',
            external_name='Connecticut',
            create_method_kwargs=dict(geography='POINT(-73.7 41.6)')
        )

        # Create a city within one of the states, again represented by
        # a point rather than an outline.
        lake_placid, is_new = get_one_or_create(
            self._db, Place, type=Place.CITY, external_id='1234',
            external_name='Lake Placid',
            parent=new_york,
            create_method_kwargs=dict(
                geography='POINT(-73.59 44.17)'
            )
        )        
        eq_(new_york, lake_placid.parent)
        eq_([lake_placid], new_york.children)
        eq_([], new_mexico.children)
        
        # Query the database to find states ordered by distance from
        # Lake Placid.
        distance = func.ST_Distance(lake_placid.geography, Place.geography)
        places = self._db.query(Place).filter(
            Place.type==Place.STATE).order_by(distance).add_columns(distance)
        
        # We can find the distance in kilometers between the 'Lake
        # Placid' point and the points representing the other states.
        eq_(
            [
                ("New York", 172),
                ("Connecticut", 285),
                ("New Mexico", 2998)
            ],
            [(x[0].external_name, int(x[1]/1000)) for x in places]
        )

    def test_aliases(self):
        new_york, is_new = get_one_or_create(
            self._db, Place, type=Place.STATE, external_id='04',
            external_name='New York',
            create_method_kwargs=dict(geography='POINT(-75 43)')
        )
        alias, is_new = get_one_or_create(
            self._db, PlaceAlias, place=new_york,
            name='New York State', language='eng'
        )
        eq_([alias], new_york.aliases)
        

class TestLibrary(DatabaseTest):

    def test_library_service_area(self):
        zip = self.zip_10018
        nypl = self._library("New York Public Library", service_areas=[zip])
        [service_area] = nypl.service_areas
        eq_(zip, service_area.place)
        eq_(nypl, service_area.library)

    def test_nearby(self):
        # Create two libraries. One serves New York City, and one serves
        # the entire state of Connecticut.
        nypl = self._library(
            "New York Public Library", service_areas=[self.new_york_city]
        )
        ct_state = self._library(
            "Connecticut State Library", service_areas=[self.connecticut_state]
        )

        # From this point in Brooklyn, NYPL is the closest library.
        # NYPL's service area includes that point, so the distance is
        # zero. The service area of CT State (i.e. the Connecticut
        # border) is only 44 kilometers away, so it also shows up.
        [(lib1, d1), (lib2, d2)] = Library.nearby(self._db, 40.65, -73.94)

        eq_(0, d1)
        eq_(nypl, lib1)

        eq_(44, int(d2/1000))
        eq_(ct_state, lib2)

        # From this point in Connecticut, CT State is the closest
        # library (0 km away), so it shows up first, but NYPL (61 km
        # away) also shows up as a possibility.
        [(lib1, d1), (lib2, d2)] = Library.nearby(self._db, 41.3, -73.3)
        eq_(ct_state, lib1)
        eq_(0, d1)
        
        eq_(nypl, lib2)
        eq_(61, int(d2/1000))
                
        # From this point in Pennsylvania, NYPL shows up (142km away) but
        # CT State does not.
        [(lib1, d1)] = Library.nearby(self._db, 40, -75.8)
        eq_(nypl, lib1)
        eq_(142, int(d1/1000))

        # If we only look within a 100km radius, then there are no
        # libraries near that point in Pennsylvania.
        eq_([], Library.nearby(self._db, 40, -75.8, 100).all())
