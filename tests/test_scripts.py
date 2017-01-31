from nose.tools import set_trace
from StringIO import StringIO

from model import (
    Place,
)
from scripts import (
    LoadPlacesScript,
)
from . import (
    DatabaseTest
)

class TestLoadPlacesScript(DatabaseTest):

    def test_run(self):
        test_ndjson = """{"parent_id": null, "name": "United States", "full_name": null, "aliases": [], "type": "nation", "abbreviated_name": "US", "id": "US"}
{"type": "Point", "coordinates": [-159.459551, 54.948652]}
{"parent_id": "US", "name": "Alabama", "full_name": null, "aliases": [], "type": "state", "abbreviated_name": "AL", "id": "01"}
{"type": "Point", "coordinates": [-88.053375, 30.506987]}
{"parent_id": "01", "name": "Montgomery", "full_name": null, "aliases": [], "type": "city", "abbreviated_name": null, "id": "0151000"}
{"type": "Point", "coordinates": [-86.034128, 32.302979]}"""
        script = LoadPlacesScript(self._db)

        # Run the script...
        script.run(stdin=StringIO(test_ndjson))

        # ...and import three places into the database.
        places = self._db.query(Place).all()
        eq_(["United States", "Alabama", "Montgomery"],
            [x.external_name for x in places])
        eq_(["US", "01", "0151000"], [x.external_id for x in places])

