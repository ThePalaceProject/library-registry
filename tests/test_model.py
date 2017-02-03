from nose.tools import (
    eq_,
    set_trace,
)
from sqlalchemy import func

from model import (
    get_one,
    get_one_or_create,
    Library,
    LibraryAlias,
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
            create_method_kwargs=dict(geometry='SRID=4326;POINT(-75 43)')
        )
        eq_(True, is_new)
        
        new_mexico, is_new = get_one_or_create(
            self._db, Place, type=Place.STATE, external_id='21',
            external_name='New Mexico',
            create_method_kwargs=dict(geometry='SRID=4326;POINT(-106 34)')
        )
        
        connecticut, is_new = get_one_or_create(
            self._db, Place, type=Place.STATE, external_id='14',
            external_name='Connecticut',
            create_method_kwargs=dict(geometry='SRID=4326;POINT(-73.7 41.6)')
        )

        # Create a city within one of the states, again represented by
        # a point rather than an outline.
        lake_placid, is_new = get_one_or_create(
            self._db, Place, type=Place.CITY, external_id='1234',
            external_name='Lake Placid',
            parent=new_york,
            create_method_kwargs=dict(
                geometry='SRID=4326;POINT(-73.59 44.17)'
            )
        )        
        eq_(new_york, lake_placid.parent)
        eq_([lake_placid], new_york.children)
        eq_([], new_mexico.children)
        
        # Query the database to find states ordered by distance from
        # Lake Placid.
        distance = func.ST_Distance_Sphere(
            lake_placid.geometry, Place.geometry
        )
        places = self._db.query(Place).filter(
            Place.type==Place.STATE).order_by(distance).add_columns(distance)
        
        # We can find the distance in kilometers between the 'Lake
        # Placid' point and the points representing the other states.
        eq_(
            [
                ("New York", 172),
                ("Connecticut", 285),
                ("New Mexico", 2993)
            ],
            [(x[0].external_name, int(x[1]/1000)) for x in places]
        )

    def test_aliases(self):
        new_york, is_new = get_one_or_create(
            self._db, Place, type=Place.STATE, external_id='04',
            external_name='New York',
            create_method_kwargs=dict(geometry='SRID=4326;POINT(-75 43)')
        )
        alias, is_new = get_one_or_create(
            self._db, PlaceAlias, place=new_york,
            name='New York State', language='eng'
        )
        eq_([alias], new_york.aliases)

    def test_served_by(self):
        zip = self.zip_10018
        nyc = self.new_york_city
        new_york = self.new_york_state
        connecticut = self.connecticut_state

        # There are two libraries here...
        nypl = self._library("New York Public Library", service_areas=[nyc])
        ct_state = self._library(
            "Connecticut State Library", service_areas=[connecticut]
        )

        # ...but only one serves the 10018 ZIP code.
        eq_([nypl], zip.served_by().all())

        eq_([nypl], nyc.served_by().all())
        eq_([ct_state], connecticut.served_by().all())

        # New York and Connecticut share a border, and the Connecticut
        # state library serves the entire state, including the
        # border. According to PostGIS 'intersect' logic, Connecticut
        # intersects New York at the border. This implies that the
        # Connecticut state library also serves New York state. We
        # avoid this by, when searching for libraries on the state or
        # national level, only considering results located in the same
        # state or nation.
        eq_([nypl], new_york.served_by().all())
        

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

    def test_query_cleanup(self):
        m = Library.query_cleanup

        eq_("the library", m("THE LIBRARY"))
        eq_("the library", m("\tthe   library\n\n"))
        eq_("the library", m("the libary"))

    def test_as_postal_code(self):
        m = Library.as_postal_code
        eq_("93203", m("93203"))
        eq_("93203", m("93203-1234"))
        eq_(None, m("the library"))

        # A UK post code is not currently recognized.
        eq_(None, m("AB1 0AA"))

    def test_query_parts(self):
        m = Library.query_parts
        eq_((None, "93203", Place.POSTAL_CODE), m("93203"))
        eq_(("new york public library", "new york", None),
            m("new york public library"))        
        eq_(("queens library", "queens", None), m("queens library"))
        eq_(("kern county library", "kern", Place.COUNTY),
            m("kern county library"))
        eq_(("lapl", "lapl", None), m("lapl"))

    def test_search_by_name(self):
        def search(x):
            return list(Library.search_by_name(self._db, x))

        brooklyn, is_new = get_one_or_create(
            self._db, Library, name="Brooklyn Public Library"
        )

        eq_([brooklyn], search("Brooklyn Public Library"))

        # We can tolerate a small number of typos in the official name
        # of the library.
        eq_([brooklyn], search(self._db, "broklyn public library"))
        
        boston, is_new = get_one_or_create(
            self._db, Library, name="Boston Public Library"
        )

        for library in (brooklyn, boston):
            get_one_or_create(
                self._db, LibraryAlias, name="BPL", language=None,
                library=library
            )
        eq_(
            set([brooklyn, boston]), set(search("bpl"))
        )

        # We do not tolerate typos in short names.
        eq_([], search("OPL"))
        

    def test_search_by_location(self):
        # We know about the NYPL, which serves Manhattan (an alias for
        # New York City), and we know about the Kansas State Library,
        # which servers Manhattan, KS.
        nypl = self.nypl
        kansas_state = self.kansas_state_library
        connecticut_state = self.connecticut_state_library
        
        libraries = list(Library.search_by_name(self._db, "manhattan"))

        # Our query finds both libraries.
        pass
