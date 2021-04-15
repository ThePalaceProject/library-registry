import json
import pytest
from io import StringIO

from config import Configuration
from emailer import Emailer
from model import (
    ConfigurationSetting,
    ExternalIntegration,
    Library,
    Place,
    create,
    get_one,
)
from problem_details import INVALID_INTEGRATION_DOCUMENT
from registrar import LibraryRegistrar
from scripts import (
    AddLibraryScript,
    ConfigureEmailerScript,
    ConfigureIntegrationScript,
    ConfigureSiteScript,
    ConfigureVendorIDScript,
    LibraryScript,
    LoadPlacesScript,
    RegistrationRefreshScript,
    SearchLibraryScript,
    SearchPlacesScript,
    SetCoverageAreaScript,
    ShowIntegrationsScript,
)
from testing import MockPlace
from . import (
    DatabaseTest
)


class TestLibraryScript(DatabaseTest):

    def test_libraries(self):

        library = self._library(name="The Library")
        ignored = self._library(name="Ignored Library")

        class Mock(LibraryScript):
            # Mock of LibraryScript that returns a special value
            # when all_libraries is called.

            all_libraries_return_value = object()
            @property
            def all_libraries(self):
                return self.all_libraries_return_value
        script = Mock(self._db)

        # Any library can be processed if it's identified by name.
        for l in library, ignored:
            assert script.libraries(l.name) == [l]
        with pytest.raises(ValueError) as exc:
            script.libraries("Nonexistent Library")
        assert "No library with name 'Nonexistent Library'" in str(exc.value)

        # If no library is identified by name, the output of
        # all_libraries is used as the list of libraries.
        assert script.libraries() == script.all_libraries_return_value

    def test_all_libraries(self):
        # Three libraries, one in each state.
        production = self._library()
        testing = self._library(library_stage=Library.TESTING_STAGE)
        cancelled = self._library(library_stage=Library.CANCELLED_STAGE)

        # The all_libraries property omits the cancelled library.
        script = LibraryScript(self._db)
        assert set(script.all_libraries) == set([production, testing])


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
        assert set([x.external_name for x in places]) == set(["United States", "Alabama", "Montgomery"])            
        assert set([x.external_id for x in places]) == set(["US", "01", "0151000"])


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
                '--authentication-url=https://circulation.librarysimplified.org/NYNYPL/authentication_document',
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

        assert library.name == "The New York Public Library"
        assert library.internal_urn.startswith("urn:uuid")
        assert library.authentication_url == "https://circulation.librarysimplified.org/NYNYPL/authentication_document"
        assert library.web_url == "https://nypl.org/"
        assert library.opds_url == "https://circulation.librarysimplified.org/"
        assert library.description == "Serving the five boroughs of New York, NY."
        assert library.short_name == "NYNYPL"
        assert library.shared_secret == "12345"

        [alias] = library.aliases
        assert alias.name == "NYPL"
        assert alias.language == "eng"

        assert [x.place for x in library.service_areas] == [nyc]


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
        assert actual_output == "%s: %s\n" % (nypl.name, nypl.opds_url)

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
        actual = output.getvalue()
        assert "setting1='value1'" in actual
        assert """setting2='[1,2,"3"]'""" in actual

        assert ConfigurationSetting.sitewide(self._db, "setting1").value == "value1"
        assert ConfigurationSetting.sitewide(self._db, "setting2").value == '[1,2,"3"]'
        assert ConfigurationSetting.sitewide(self._db, "secret_setting").value == "secretvalue"

        # If we run again with --show-secrets, the secret is shown.
        output = StringIO()
        script.do_run(self._db, ["--show-secrets"], output)
        actual = output.getvalue()
        assert "secret_setting='secretvalue'" in actual
        assert "setting1='value1'" in actual
        assert """setting2='[1,2,"3"]'""" in actual


class TestShowIntegrationsScript(DatabaseTest):

    def test_with_no_integrations(self):
        output = StringIO()
        ShowIntegrationsScript().do_run(self._db, output=output)
        assert output.getvalue() == "No integrations found.\n"

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

        assert output.getvalue() == expect_1 + "\n" + expect_2 + "\n"


        # We can tell the script to only list a single integration.
        output = StringIO()
        ShowIntegrationsScript().do_run(
            self._db,
            cmd_args=["--name=Integration 2"],
            output=output
        )
        assert output.getvalue() == expect_2 + "\n"

        # We can tell the script to include the integration secrets
        output = StringIO()
        ShowIntegrationsScript().do_run(
            self._db,
            cmd_args=["--show-secrets"],
            output=output
        )
        expect_1 = "\n".join(i1.explain(include_secrets=True))
        expect_2 = "\n".join(i2.explain(include_secrets=True))
        assert output.getvalue() == expect_1 + "\n" + expect_2 + "\n"


class TestConfigureIntegrationScript(DatabaseTest):

    def test_load_integration(self):
        m = ConfigureIntegrationScript._integration

        with pytest.raises(ValueError) as exc:
            m(self._db, None, None, "protocol", None)
        assert "An integration must by identified by either ID, name, or the combination of protocol and goal." in str(exc.value)

        with pytest.raises(ValueError) as exc:
            m(self._db, "notanid", None, None, None)
        assert "No integration with ID notanid." in str(exc.value)

        with pytest.raises(ValueError) as exc:
            m(self._db, None, "Unknown integration", None, None)
        assert 'No integration with name "Unknown integration". To create it, you must also provide protocol and goal.' in str(exc.value)

        integration, ignore = create(
            self._db, ExternalIntegration,
            protocol="Protocol", goal="Goal"
        )
        integration.name = "An integration"
        assert m(self._db, integration.id, None, None, None) == integration        
        assert m(self._db, None, integration.name, None, None) == integration
        assert m(self._db, None, None, "Protocol", "Goal") == integration

        # An integration may be created given a protocol and goal.
        integration2 = m(self._db, None, "I exist now", "Protocol", "Goal2")
        assert integration2 != integration
        assert integration2.protocol == "Protocol"
        assert integration2.goal == "Goal2"
        assert integration2.name == "I exist now"

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
        assert output.getvalue() == expect_output


class TestRegistrationRefreshScript(DatabaseTest):

    def test_run(self):
        # Verify that run() instantiates a LibraryRegistrar using .registrar,
        # then calls its reregister() method on every library that it's
        # been asked to handle.
        success_library = self._library(name="Success")
        failure_library = self._library(name="Failure")

        class MockRegistrar(object):
            reregistered = []

            def reregister(self, library):
                # Pretend to reregister a library.
                self.reregistered.append(library)

                # The difference between success and failure isn't
                # tested here; this just lets us check that both code
                # paths execute without crashing.
                if library is success_library:
                    # When registration is not a problem detail
                    # document, the return value is ignored.
                    return object()
                else:
                    # When the return value is a problem detail
                    # document, reregistration is assumed to be a
                    # failure.
                    return INVALID_INTEGRATION_DOCUMENT

        mock_registrar = MockRegistrar()
        class MockScript(RegistrationRefreshScript):
            def libraries(self, library_name):
                # Return a predefined set of libraries.
                self.libraries_called_with = library_name
                return [success_library, failure_library]

            @property
            def registrar(self):
                # Return a fake LibraryRegistrar.
                return mock_registrar
        script = MockScript(self._db)

        # Run with no arguments -- this will process all libraries in
        # script.libraries.
        script.run(cmd_args=[])

        # LibraryRegistrar.reregister() was called twice: on
        # success_library and on failure_library.
        assert script.libraries_called_with is None
        assert mock_registrar.reregistered == [success_library, failure_library]

        # We can also tell the script to reregister one specific
        # library. This tests that the command line is parsed and a
        # library name is passed into libraries(), even though our
        # mock implementation ignores the library name.
        script.run(cmd_args=["--library=Library1"])
        assert script.libraries_called_with == "Library1"

    def test_registrar(self):
        # Verify that the normal, non-mocked value of script.registrar
        # is a LibraryRegistrar.
        script = RegistrationRefreshScript(self._db)
        registrar = script.registrar
        assert isinstance(registrar, LibraryRegistrar)
        assert registrar._db == self._db

class TestSetCoverageAreaScript(DatabaseTest):

    def test_argument_parsing(self):
        library = self._library()
        s = SetCoverageAreaScript(_db=self._db)

        # You can run the script without specifying any areas, to
        # see a library's current areas.
        s.run(["--library=%s" % library.name], place_class=MockPlace)

    def test_unrecognized_place(self):
        library = self._library()
        s = SetCoverageAreaScript(_db=self._db)
        for arg in ['service-area', 'focus-area']:
            args = ["--library=%s" % library.name,
                    '--%s={"US": "San Francisco"}' % arg]
            with pytest.raises(ValueError) as exc:
                s.run(args, place_class=MockPlace)
            assert "Unknown places:" in str(exc.value)

    def test_ambiguous_place(self):

        MockPlace.by_name["OO"] = MockPlace.AMBIGUOUS

        library = self._library()
        s = SetCoverageAreaScript(_db=self._db)
        for arg in ['service-area', 'focus-area']:
            args = ["--library=%s" % library.name,
                    '--%s={"OO": "everywhere"}' % arg]
            with pytest.raises(ValueError) as exc:
                s.run(args, place_class=MockPlace)
            assert "Ambiguous places:" in str(exc.value)
        MockPlace.by_name = {}

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
        assert area.place == us

        # Setting a focus area and not a service area treats 'everywhere'
        # as the service area.
        uk = self._place(type=Place.NATION, abbreviated_name='UK')
        args = ["--library=%s" % library.name,
                '--focus-area={"UK": "everywhere"}']
        s.run(args)
        places = [x.place for x in library.service_areas]
        assert len(places) == 2
        assert uk in places
        assert Place.everywhere(self._db) in places

        # The library's former ServiceAreas have been removed.
        assert us not in places

        # If a default nation is set, you can name a single place as
        # your service area.
        ConfigurationSetting.sitewide(
            self._db, Configuration.DEFAULT_NATION_ABBREVIATION
        ).value = "US"
        ut = self._place(type=Place.STATE, abbreviated_name='UT', parent=us)

        args = ["--library=%s" % library.name,
                '--service-area=UT']
        s.run(args)
        [area] = library.service_areas
        assert area.place == ut


class TestConfigureEmailerScript(DatabaseTest):

    def test_run(self):
        class Mock(Emailer):
            sent = None
            def send(self, template_name, to_address):
                Mock.sent = (template_name, to_address)

        cmd_args = [
            "--host=a_host",
            "--port=25",
            "--username=a_user",
            "--password=a_password",
            "--from-address=from@example.com",
            "--from-name=Administrator",
            "--test-address=you@example.com"
        ]
        script = ConfigureEmailerScript(self._db)
        script.do_run(
            self._db,
            cmd_args=cmd_args,
            emailer_class=Mock
        )

        # The ExternalIntegration is properly configured.
        emailer = Emailer._sitewide_integration(self._db)
        assert emailer.username == "a_user"
        assert emailer.password == "a_password"
        assert emailer.url == "a_host"
        assert emailer.setting(Emailer.PORT).int_value == 25
        assert emailer.setting(Emailer.FROM_ADDRESS).value == "from@example.com"
        assert emailer.setting(Emailer.FROM_NAME).value == "Administrator"

        # An email was sent out to the test address.
        template, to = Mock.sent
        assert template == "test"
        assert to == "you@example.com"


class TestConfigureVendorIDScript(DatabaseTest):

    def test_run(self):
        cmd_args = [
            "--vendor-id=LIBR",
            "--node-value=abc12",
            "--delegate=http://server1/AdobeAuth/",
            "--delegate=http://server2/AdobeAuth/",
        ]
        script = ConfigureVendorIDScript(self._db)
        script.do_run(self._db, cmd_args=cmd_args)

        # The ExternalIntegration is properly configured.
        integration = ExternalIntegration.lookup(
            self._db, ExternalIntegration.ADOBE_VENDOR_ID,
            ExternalIntegration.DRM_GOAL
        )
        assert integration.setting(Configuration.ADOBE_VENDOR_ID).value == "LIBR"
        assert integration.setting(Configuration.ADOBE_VENDOR_ID_NODE_VALUE).value == "abc12"
        assert integration.setting(Configuration.ADOBE_VENDOR_ID_DELEGATE_URL).json_value == ["http://server1/AdobeAuth/", "http://server2/AdobeAuth/"]

        # The script won't run if --node-value or --delegate have obviously
        # wrong values.
        cmd_args = [
            "--vendor-id=LIBR",
            "--node-value=not a hex number",
        ]
        with pytest.raises(ValueError) as exc:
            script.do_run(self._db, cmd_args=cmd_args)
        assert "invalid literal for int" in str(exc.value)

        cmd_args = [
            "--vendor-id=LIBR",
            "--node-value=abce",
            "--delegate=http://random-site/",
        ]
        with pytest.raises(ValueError) as exc:
            script.do_run(self._db, cmd_args=cmd_args)
        assert "Invalid delegate: http://random-site/" in str(exc.value)
