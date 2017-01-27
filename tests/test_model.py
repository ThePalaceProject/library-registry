from nose.tools import (
    eq_,
    set_trace,
)
from sqlalchemy import func

from model import (
    get_one,
    get_one_or_create,
    Place,
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
