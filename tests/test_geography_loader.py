from StringIO import StringIO
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

from geography_loader import GeographyLoader


class TestGeographyLoader(DatabaseTest):

    def setup(self):
        super(TestGeographyLoader, self).setup()
        self.loader = GeographyLoader(self._db)
    
    def test_load(self):       
        # Load a place identified by a GeoJSON Polygon.
        metadata = '{"parent_id": null, "name": "77977", "id": "77977", "type": "postal_code"}'
        geography = '{"type": "Polygon", "coordinates": [[[-96.840066, 28.683039], [-96.830637, 28.690131], [-96.835048, 28.693599], [-96.833515, 28.694926], [-96.82657, 28.699584], [-96.822495, 28.695826], [-96.821248, 28.696391], [-96.814249, 28.700983], [-96.772337, 28.722765], [-96.768804, 28.725363], [-96.768564, 28.725046], [-96.767246, 28.723276], [-96.765295, 28.722084], [-96.764568, 28.720456], [-96.76254, 28.718483], [-96.763087, 28.717521], [-96.761814, 28.716488], [-96.761088, 28.713623], [-96.762231, 28.712798], [-96.75967, 28.709812], [-96.781093, 28.677548], [-96.784803, 28.675363], [-96.793788, 28.669546], [-96.791527, 28.667603], [-96.808567, 28.678507], [-96.81505, 28.682946], [-96.820191, 28.684517], [-96.827178, 28.679867], [-96.828626, 28.681719], [-96.831309, 28.680451], [-96.83565, 28.677724], [-96.840066, 28.683039]]]}'
        texas_zip, is_new = self.loader.load(metadata, geography)
        eq_(True, is_new)
        eq_("77977", texas_zip.external_id)
        eq_("77977", texas_zip.external_name)
        eq_(None, texas_zip.parent)
        eq_("postal_code", texas_zip.type)
       
        # Load another place identified by a GeoJSON Point.
        metadata = '{"parent_id": null, "name": "New York", "type": "state", "abbreviated_name": "NY", "id": "NY", "full_name": "New York"}'
        geography = '{"type": "Point", "coordinates": [-75, 43]}'
        new_york, is_new = self.loader.load(metadata, geography)
        eq_("NY", new_york.abbreviated_name)
        eq_("New York", new_york.external_name)
        eq_(True, is_new)
        
        # We can measure the distance in kilometers between New York
        # and Texas. This verifies that the GeoJSON shapes are
        # imported as real-world geographies, not abstract
        # geometries.
        distance_func = func.ST_Distance(
            new_york.geography, texas_zip.geography
        )
        distance_qu = self._db.query().add_columns(distance_func)
        [[distance]] = distance_qu.all()
        eq_(2511, int(distance/1000))
        
        # Not implemented yet.
        assert not hasattr(new_york, 'aliases')

        # If we load the same place again, but with a different geography,
        # the Place object is updated.
        geography = '{"type": "Point", "coordinates": [-74, 44]}'
        new_york_2, is_new = self.loader.load(metadata, geography)
        eq_(False, is_new)
        eq_(new_york, new_york_2)

        # This changes the distance between the two points.
        distance_func = func.ST_Distance(
            new_york_2.geography, texas_zip.geography
        )
        distance_qu = self._db.query().add_columns(distance_func)
        [[distance]] = distance_qu.all()
        eq_(2638, int(distance/1000))
        
    def test_load_ndjson(self):
        # Load a small NDJSON "file" containing information about
        # three places.
        test_ndjson = """{"parent_id": null, "name": "United States", "full_name": null, "aliases": [], "type": "nation", "abbreviated_name": "US", "id": "US"}
{"type": "Point", "coordinates": [-159.459551, 54.948652]}
{"parent_id": "US", "name": "Alabama", "full_name": null, "aliases": [], "type": "state", "abbreviated_name": "AL", "id": "01"}
{"type": "Point", "coordinates": [-88.053375, 30.506987]}
{"parent_id": "01", "name": "Montgomery", "full_name": null, "aliases": [], "type": "city", "abbreviated_name": null, "id": "0151000"}
{"type": "Point", "coordinates": [-86.034128, 32.302979]}"""
        input = StringIO(test_ndjson)
        [(us, ignore), (alabama, ignore), (montgomery, ignore)] = list(
            self.loader.load_ndjson(input)
        )

        # All three places were loaded as Place objects and their
        # relationships to each other were maintained.
        assert isinstance(us, Place)
        assert isinstance(alabama, Place)
        assert isinstance(montgomery, Place)
        eq_(None, us.parent)
        eq_(us, alabama.parent)
        eq_(alabama, montgomery.parent)
        
        # We can measure the distance in kilometers between the point
        # chosen to represent 'Montgomery' and the point chosen to
        # represent 'Alabama'.
        distance_func = func.ST_Distance(
            montgomery.geography, alabama.geography
        )
        [[distance]] = self._db.query().add_columns(distance_func).all()
        eq_(276, int(distance/1000))
