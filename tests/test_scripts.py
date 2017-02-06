from nose.tools import (
    set_trace,
    eq_,
)
from StringIO import StringIO

from model import (
    Library,
    Place,
)
from scripts import (
    AddLibraryScript,
    LoadPlacesScript,
    SearchLibraryScript,
    SearchPlacesScript,
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
        eq_(set(["United States", "Alabama", "Montgomery"]),
            set([x.external_name for x in places]))
        eq_(set(["US", "01", "0151000"]), set([x.external_id for x in places]))


class TestSearchPlacesScript(DatabaseTest):

    def test_run(self):
        nys = self.new_york_state
        ct = self.connecticut_state
        nyc = self.new_york_city

        # Run the script...
        output = StringIO()
        script = SearchPlacesScript(self._db)
        script.run(["New York"], stdout=output)

        # We found the two places called 'New York', but not the other
        # place.
        actual_output = output.getvalue()
        assert repr(nys) in actual_output
        assert repr(nyc) in actual_output
        assert 'Connecticut' not in actual_output


class TestAddLibraryScript(DatabaseTest):

    def test_run(self):
        nyc = self.new_york_city
        args = ['--name=The New York Public Library',
                '--urn=1236662b-66cf-3068-af58-95385f299b4f',
                '--place=' + nyc.external_id,
                '--alias=NYPL',
                '--web=https://nypl.org/',
                '--opds=https://circulation.librarysimplified.org/',
                '--description=Serving the five boroughs of New York, NY.']
        script = AddLibraryScript(self._db)
        script.run(cmd_args=args)

        # A library was created with the given specs.
        [library] = self._db.query(Library).all()

        eq_("The New York Public Library", library.name)
        eq_("1236662b-66cf-3068-af58-95385f299b4f", library.urn)
        eq_("https://nypl.org/", library.web_url)
        eq_("https://circulation.librarysimplified.org/", library.opds_url)
        eq_("Serving the five boroughs of New York, NY.", library.description)

        [alias] = library.aliases
        eq_("NYPL", alias.name)
        eq_("eng", alias.language)

        eq_([nyc], [x.place for x in library.service_areas])


class TestSearchPlacesScript(DatabaseTest):

    def test_run(self):
        nys = self.new_york_state
        nypl = self.nypl
        csl = self.connecticut_state_library
        zip = self.zip_10018
        ct = self.connecticut_state
        nyc = self.new_york_city
        nypl.opds_url = "http://opds/"
        
        # Run the script...
        output = StringIO()
        script = SearchLibraryScript(self._db)
        script.run(cmd_args=["10018"], stdout=output)

        # We found the library whose service area overlaps 10018
        # (NYPL), but not the other library.
        actual_output = output.getvalue()
        eq_("%s: %s\n" % (nypl.name, nypl.opds_url), actual_output)

