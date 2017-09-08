import json
from nose.tools import (
    assert_raises_regexp,
    set_trace,
    eq_,
)
from StringIO import StringIO

from model import (
    ConfigurationSetting,
    ExternalIntegration,
    Library,
    Place,
    create,
    get_one,
)
from scripts import (
    AddLibraryScript,
    ConfigureIntegrationScript,
    ConfigureSiteScript,
    LoadPlacesScript,
    SearchLibraryScript,
    SearchPlacesScript,
    SetCoverageAreaScript,
    ShowIntegrationsScript,
)
from testing import MockPlace
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
        script.run(cmd_args=[], stdin=StringIO(test_ndjson))

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
                '--description=Serving the five boroughs of New York, NY.',
                '--short-name=NYNYPL',
                '--shared-secret=12345',
        ]
        script = AddLibraryScript(self._db)
        script.run(cmd_args=args)

        # A library was created with the given specs.
        [library] = self._db.query(Library).all()

        eq_(u"The New York Public Library", library.name)
        eq_(u"1236662b-66cf-3068-af58-95385f299b4f", library.urn)
        eq_(u"https://nypl.org/", library.web_url)
        eq_(u"https://circulation.librarysimplified.org/", library.opds_url)
        eq_(u"Serving the five boroughs of New York, NY.", library.description)
        eq_(u"NYNYPL", library.short_name)
        eq_(u"12345", library.shared_secret)
        
        [alias] = library.aliases
        eq_("NYPL", alias.name)
        eq_("eng", alias.language)

        eq_([nyc], [x.place for x in library.service_areas])


class TestSearchLibraryScript(DatabaseTest):

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

class TestConfigureSiteScript(DatabaseTest):

    def test_settings(self):
        script = ConfigureSiteScript()
        output = StringIO()
        script.do_run(
            self._db, [
                "--setting=setting1=value1",
                "--setting=setting2=[1,2,\"3\"]",
                "--setting=secret_setting=secretvalue",
            ],
            output
        )
        # The secret was set, but is not shown.
        eq_("""Current site-wide settings:
setting1='value1'
setting2='[1,2,"3"]'
""",
            output.getvalue()
        )
        eq_("value1", ConfigurationSetting.sitewide(self._db, "setting1").value)
        eq_('[1,2,"3"]', ConfigurationSetting.sitewide(self._db, "setting2").value)
        eq_("secretvalue", ConfigurationSetting.sitewide(self._db, "secret_setting").value)

        # If we run again with --show-secrets, the secret is shown.
        output = StringIO()
        script.do_run(self._db, ["--show-secrets"], output)
        eq_("""Current site-wide settings:
secret_setting='secretvalue'
setting1='value1'
setting2='[1,2,"3"]'
""",
            output.getvalue()
        )

class TestShowIntegrationsScript(DatabaseTest):

    def test_with_no_integrations(self):
        output = StringIO()
        ShowIntegrationsScript().do_run(self._db, output=output)
        eq_("No integrations found.\n", output.getvalue())

    def test_with_multiple_integrations(self):
        i1, ignore = create(
            self._db, ExternalIntegration,
            name="Integration 1",
            goal="Goal",
            protocol=ExternalIntegration.ADOBE_VENDOR_ID
        )
        i2, ignore = create(
            self._db, ExternalIntegration,
            name="Integration 2",
            goal="Goal",
            protocol=ExternalIntegration.ADOBE_VENDOR_ID
        )

        # The output of this script is the result of running explain()
        # on both integrations.
        output = StringIO()
        ShowIntegrationsScript().do_run(self._db, output=output)
        expect_1 = "\n".join(i1.explain(include_secrets=False))
        expect_2 = "\n".join(i2.explain(include_secrets=False))
        
        eq_(expect_1 + "\n" + expect_2 + "\n", output.getvalue())


        # We can tell the script to only list a single integration.
        output = StringIO()
        ShowIntegrationsScript().do_run(
            self._db,
            cmd_args=["--name=Integration 2"],
            output=output
        )
        eq_(expect_2 + "\n", output.getvalue())
        
        # We can tell the script to include the integration secrets
        output = StringIO()
        ShowIntegrationsScript().do_run(
            self._db,
            cmd_args=["--show-secrets"],
            output=output
        )
        expect_1 = "\n".join(i1.explain(include_secrets=True))
        expect_2 = "\n".join(i2.explain(include_secrets=True))
        eq_(expect_1 + "\n" + expect_2 + "\n", output.getvalue())
        

class TestConfigureIntegrationScript(DatabaseTest):
    
    def test_load_integration(self):
        m = ConfigureIntegrationScript._integration

        assert_raises_regexp(
            ValueError,
            "An integration must by identified by either ID, name, or the combination of protocol and goal.",
            m, self._db, None, None, "protocol", None
        )

        assert_raises_regexp(
            ValueError,
            "No integration with ID notanid.",
            m, self._db, "notanid", None, None, None
        )

        assert_raises_regexp(
            ValueError,
            'No integration with name "Unknown integration". To create it, you must also provide protocol and goal.',
            m, self._db, None, "Unknown integration", None, None
        )
        
        integration, ignore = create(
            self._db, ExternalIntegration,
            protocol="Protocol", goal="Goal"
        )
        integration.name = "An integration"
        eq_(integration,
            m(self._db, integration.id, None, None, None)
        )

        eq_(integration,
            m(self._db, None, integration.name, None, None)
        )

        eq_(integration,
            m(self._db, None, None, "Protocol", "Goal")
        )

        # An integration may be created given a protocol and goal.
        integration2 = m(self._db, None, "I exist now", "Protocol", "Goal2")
        assert integration2 != integration
        eq_("Protocol", integration2.protocol)
        eq_("Goal2", integration2.goal)
        eq_("I exist now", integration2.name)
        
    def test_add_settings(self):
        script = ConfigureIntegrationScript()
        output = StringIO()

        script.do_run(
            self._db, [
                "--protocol=aprotocol",
                "--goal=agoal",
                "--setting=akey=avalue",
            ],
            output
        )

        # An ExternalIntegration was created and configured.
        integration = get_one(self._db, ExternalIntegration,
                              protocol="aprotocol", goal="agoal")

        expect_output = "Configuration settings stored.\n" + "\n".join(integration.explain()) + "\n"
        eq_(expect_output, output.getvalue())
       

class TestSetCoverageAreaScript(DatabaseTest):

    def test_argument_parsing(self):
        library = self._library()
        s = SetCoverageAreaScript(_db=self._db)
        base = ["--library=%s" % library.name]
        assert_raises_regexp(
            Exception,
            "Either --service-area or --focus-area must be specified",
            s.run, base, place_class=MockPlace
        )

        for arg in ['service-area', 'focus-area']:
            args = base + ['--%s=NotJSON' % arg]
            print args
            assert_raises_regexp(
                ValueError,
                "Invalid JSON:",
                s.run, args, place_class=MockPlace
            )

            not_a_place = json.dumps("Not a place")
            args = base + ['--%s=%s' % (arg, not_a_place)]
            assert_raises_regexp(
                ValueError,
                "Not a place document:",
                s.run, args, place_class=MockPlace
            )

    def test_unrecognized_place(self):      
        library = self._library()
        s = SetCoverageAreaScript(_db=self._db)
        for arg in ['service-area', 'focus-area']:        
            args = ["--library=%s" % library.name, 
                    '--%s={"US": "San Francisco"}' % arg]
            assert_raises_regexp(
                ValueError,
                "Unknown places:",
                s.run, args, place_class=MockPlace
            )

    def test_ambiguous_place(self):

        MockPlace.by_name["OO"] = MockPlace.AMBIGUOUS

        library = self._library()
        s = SetCoverageAreaScript(_db=self._db)
        for arg in ['service-area', 'focus-area']:        
            args = ["--library=%s" % library.name, 
                    '--%s={"OO": "everywhere"}' % arg]
            assert_raises_regexp(
                ValueError,
                "Ambiguous places:",
                s.run, args, place_class=MockPlace
            )

    def test_success(self):
        us = self._place(type=Place.NATION, abbreviated_name='US')
        library = self._library()
        s = SetCoverageAreaScript(_db=self._db)

        # Setting a service area with no focus area assigns that
        # service area to the library.
        args = ["--library=%s" % library.name, 
                '--service-area={"US": "everywhere"}']
        s.run(args)
        [area] = library.service_areas
        eq_(us, area.place)

        # Setting a focus area and not a service area treats 'everywhere'
        # as the service area.
        uk = self._place(type=Place.NATION, abbreviated_name='UK')
        args = ["--library=%s" % library.name,
                '--focus-area={"UK": "everywhere"}']
        s.run(args)
        places = [x.place for x in library.service_areas]
        eq_(2, len(places))
        assert uk in places
        assert Place.everywhere(self._db) in places

        # The library's former ServiceAreas have been removed.
        assert us not in places
