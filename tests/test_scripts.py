from io import StringIO

import pytest

from library_registry.config import Configuration
from library_registry.emailer import Emailer
from library_registry.model import (
    ConfigurationSetting,
    ExternalIntegration,
    Library,
    Place,
    ServiceArea,
)
from library_registry.model_helpers import (create, get_one)
from library_registry.problem_details import INVALID_INTEGRATION_DOCUMENT
from library_registry.registrar import LibraryRegistrar
from library_registry.scripts import (
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

from .mocks import MockPlace


class TestLibraryScript:
    @pytest.mark.needsdocstring
    def test_libraries(self, db_session, create_test_library):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        library = create_test_library(db_session, library_name="The Library")
        ignored = create_test_library(db_session, library_name="Ignored Library")

        class Mock(LibraryScript):
            """Mock of LibraryScript that returns a special value when all_libraries is called."""
            all_libraries_return_value = object()

            @property
            def all_libraries(self):
                return self.all_libraries_return_value

        script = Mock(db_session)

        # Any library can be processed if it's identified by name.
        for lib in library, ignored:
            assert script.libraries(lib.name) == [lib]

        with pytest.raises(ValueError) as exc:
            script.libraries("Nonexistent Library")

        assert "No library with name 'Nonexistent Library'" in str(exc.value)

        # If no library is identified by name, the output of all_libraries is used as the list of libraries.
        assert script.libraries() == script.all_libraries_return_value

    @pytest.mark.needsdocstring
    def test_all_libraries(self, db_session, create_test_library):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        # Three libraries, one in each state.
        production = create_test_library(db_session)
        testing = create_test_library(db_session, library_stage=Library.TESTING_STAGE)
        cancelled = create_test_library(db_session, library_stage=Library.CANCELLED_STAGE)

        # The all_libraries property omits the cancelled library.
        script = LibraryScript(db_session)
        assert set(script.all_libraries) == set([production, testing])


class TestLoadPlacesScript:

    @pytest.mark.needsdocstring
    def test_run(self, db_session):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        test_ndjson_lines = [
            '{"parent_id": null, "name": "United States", "full_name": null, "aliases": [], "type": "nation", "abbreviated_name": "US", "id": "US"}',   # noqa: E501
            '{"type": "Point", "coordinates": [-159.459551, 54.948652]}',
            '{"parent_id": "US", "name": "Alabama", "full_name": null, "aliases": [], "type": "state", "abbreviated_name": "AL", "id": "01"}',          # noqa: E501
            '{"type": "Point", "coordinates": [-88.053375, 30.506987]}',
            '{"parent_id": "01", "name": "Montgomery", "full_name": null, "aliases": [], "type": "city", "abbreviated_name": null, "id": "0151000"}',   # noqa: E501
            '{"type": "Point", "coordinates": [-86.034128, 32.302979]}'
        ]
        script = LoadPlacesScript(db_session)

        # Run the script...
        script.run(cmd_args=[], stdin=StringIO("\n".join(test_ndjson_lines)))

        # ...and import three places into the database.
        places = db_session.query(Place).all()
        assert set([x.external_name for x in places]) == set(["United States", "Alabama", "Montgomery"])
        assert set([x.external_id for x in places]) == set(["US", "01", "0151000"])
        for place in places:
            db_session.delete(place)
        db_session.commit()


class TestSearchPlacesScript:

    @pytest.mark.needsdocstring
    def test_run(self, db_session, new_york_state, connecticut_state, new_york_city):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        nys = new_york_state
        nyc = new_york_city

        # Run the script...
        output = StringIO()
        script = SearchPlacesScript(db_session)
        script.run(["New York"], stdout=output)

        # We found the two places called 'New York', but not the other place.
        actual_output = output.getvalue()
        assert repr(nys) in actual_output
        assert repr(nyc) in actual_output
        assert 'Connecticut' not in actual_output


class TestAddLibraryScript:
    @pytest.mark.needsdocstring
    def test_run(self, db_session, new_york_city):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        nyc = new_york_city
        args = [
            '--name=The New York Public Library',
            '--authentication-url=https://circulation.librarysimplified.org/NYNYPL/authentication_document',
            '--place=' + nyc.external_id,
            '--alias=NYPL',
            '--web=https://nypl.org/',
            '--opds=https://circulation.librarysimplified.org/',
            '--description=Serving the five boroughs of New York, NY.',
            '--short-name=NYNYPL',
            '--shared-secret=12345',
        ]
        script = AddLibraryScript(db_session)
        script.run(cmd_args=args)

        # A library was created with the given specs.
        [library] = db_session.query(Library).all()

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

        db_session.delete(library)
        db_session.commit()


class TestSearchLibraryScript:
    @pytest.mark.skip(reason="Relies on query mechanism that's being replaced")
    def test_run(
        self, db_session, new_york_state, nypl, connecticut_state_library,
        zip_10018, connecticut_state, new_york_city
    ):
        nypl.opds_url = "http://opds/"

        # Run the script...
        output = StringIO()
        script = SearchLibraryScript(db_session)
        script.run(cmd_args=["10018"], stdout=output)

        # We found the library whose service area overlaps 10018 (NYPL), but not the other library.
        actual_output = output.getvalue()
        assert actual_output == "%s: %s\n" % (nypl.name, nypl.opds_url)


class TestConfigureSiteScript:
    @pytest.mark.needsdocstring
    def test_settings(self, db_session):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        script = ConfigureSiteScript()
        output = StringIO()
        script.do_run(
            db_session, [
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

        assert ConfigurationSetting.sitewide(db_session, "setting1").value == "value1"
        assert ConfigurationSetting.sitewide(db_session, "setting2").value == '[1,2,"3"]'
        assert ConfigurationSetting.sitewide(db_session, "secret_setting").value == "secretvalue"

        # If we run again with --show-secrets, the secret is shown.
        output = StringIO()
        script.do_run(db_session, ["--show-secrets"], output)
        actual = output.getvalue()
        assert "secret_setting='secretvalue'" in actual
        assert "setting1='value1'" in actual
        assert """setting2='[1,2,"3"]'""" in actual

        for setting_obj in db_session.query(ConfigurationSetting).all():
            db_session.delete(setting_obj)
        db_session.commit()


class TestShowIntegrationsScript:
    @pytest.mark.needsdocstring
    def test_with_no_integrations(self, db_session):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        output = StringIO()
        ShowIntegrationsScript().do_run(db_session, output=output)
        assert output.getvalue() == "No integrations found.\n"

    @pytest.mark.needsdocstring
    def test_with_multiple_integrations(self, db_session):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        (i1, _) = create(db_session, ExternalIntegration, name="Integration 1",
                         goal="Goal", protocol=ExternalIntegration.ADOBE_VENDOR_ID)
        (i2, _) = create(db_session, ExternalIntegration, name="Integration 2",
                         goal="Goal", protocol=ExternalIntegration.ADOBE_VENDOR_ID)

        # The output of this script is the result of running explain() on both integrations.
        output = StringIO()
        ShowIntegrationsScript().do_run(db_session, output=output)
        expect_1 = "\n".join(i1.explain(include_secrets=False))
        expect_2 = "\n".join(i2.explain(include_secrets=False))

        assert output.getvalue() == expect_1 + "\n" + expect_2 + "\n"

        # We can tell the script to only list a single integration.
        output = StringIO()
        ShowIntegrationsScript().do_run(db_session, cmd_args=["--name=Integration 2"], output=output)
        assert output.getvalue() == expect_2 + "\n"

        # We can tell the script to include the integration secrets
        output = StringIO()
        ShowIntegrationsScript().do_run(db_session, cmd_args=["--show-secrets"], output=output)
        expect_1 = "\n".join(i1.explain(include_secrets=True))
        expect_2 = "\n".join(i2.explain(include_secrets=True))
        assert output.getvalue() == expect_1 + "\n" + expect_2 + "\n"

        db_session.delete(i1)
        db_session.delete(i2)
        db_session.commit()


class TestConfigureIntegrationScript:
    @pytest.mark.needsdocstring
    def test_load_integration(self, db_session):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        m = ConfigureIntegrationScript._integration

        with pytest.raises(ValueError) as exc:
            m(db_session, None, None, "protocol", None)
        expected = (
            "An integration must by identified by either ID, name, or the combination of protocol and goal."
        )
        assert expected in str(exc.value)

        with pytest.raises(ValueError) as exc:
            m(db_session, "notanid", None, None, None)
        assert "No integration with ID notanid." in str(exc.value)

        with pytest.raises(ValueError) as exc:
            m(db_session, None, "Unknown integration", None, None)
        expected = (
            'No integration with name "Unknown integration". To create it, you must also provide protocol and goal.'
        )
        assert expected in str(exc.value)

        (integration, _) = create(db_session, ExternalIntegration, protocol="Protocol", goal="Goal")
        integration.name = "An integration"
        assert m(db_session, integration.id, None, None, None) == integration
        assert m(db_session, None, integration.name, None, None) == integration
        assert m(db_session, None, None, "Protocol", "Goal") == integration

        # An integration may be created given a protocol and goal.
        integration2 = m(db_session, None, "I exist now", "Protocol", "Goal2")
        assert integration2 != integration
        assert integration2.protocol == "Protocol"
        assert integration2.goal == "Goal2"
        assert integration2.name == "I exist now"

        db_session.delete(integration)
        db_session.delete(integration2)
        db_session.commit()

    @pytest.mark.needsdocstring
    def test_add_settings(self, db_session):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        script = ConfigureIntegrationScript()
        output = StringIO()

        script.do_run(db_session, ["--protocol=aprotocol", "--goal=agoal", "--setting=akey=avalue"], output)

        # An ExternalIntegration was created and configured.
        integration = get_one(db_session, ExternalIntegration, protocol="aprotocol", goal="agoal")

        expect_output = "Configuration settings stored.\n" + "\n".join(integration.explain()) + "\n"
        assert output.getvalue() == expect_output

        for setting_obj in db_session.query(ConfigurationSetting).all():
            db_session.delete(setting_obj)

        for integration_obj in db_session.query(ExternalIntegration).all():
            db_session.delete(integration_obj)

        db_session.commit()


class TestRegistrationRefreshScript:
    @pytest.mark.needsdocstring
    def test_run(self, db_session, create_test_library):
        """
        Verify that run() instantiates a LibraryRegistrar using .registrar, then calls its
        reregister() method on every library that it's been asked to handle.

        GIVEN:
        WHEN:
        THEN:
        """
        success_library = create_test_library(db_session, library_name="Success")
        failure_library = create_test_library(db_session, library_name="Failure")

        class MockRegistrar:
            reregistered = []

            def reregister(self, library):
                # Pretend to reregister a library.
                self.reregistered.append(library)

                # The difference between success and failure isn't tested here; this just lets
                # us check that both code paths execute without crashing.
                if library is success_library:
                    # When registration is not a problem detail document, the return value is ignored.
                    return object()
                else:
                    # When the return value is a problem detail document, reregistration is assumed
                    # to be a failure.
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

        script = MockScript(db_session)

        # Run with no arguments -- this will process all libraries in script.libraries.
        script.run(cmd_args=[])

        # LibraryRegistrar.reregister() was called twice: on success_library and on failure_library.
        assert script.libraries_called_with is None
        assert mock_registrar.reregistered == [success_library, failure_library]

        # We can also tell the script to reregister one specific library. This tests that the command
        # line is parsed and a library name is passed into libraries(), even though our mock
        # implementation ignores the library name.
        script.run(cmd_args=["--library=Library1"])
        assert script.libraries_called_with == "Library1"

    @pytest.mark.needsdocstring
    def test_registrar(self, db_session):
        """
        Verify that the normal, non-mocked value of script.registrar is a LibraryRegistrar.

        GIVEN:
        WHEN:
        THEN:
        """
        script = RegistrationRefreshScript(db_session)
        registrar = script.registrar
        assert isinstance(registrar, LibraryRegistrar)
        assert registrar._db == db_session


class TestSetCoverageAreaScript:
    @pytest.mark.needsdocstring
    def test_argument_parsing(self, db_session, create_test_library):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        library = create_test_library(db_session)
        s = SetCoverageAreaScript(_db=db_session)

        # You can run the script without specifying any areas, to see a library's current areas.
        s.run(["--library=%s" % library.name], place_class=MockPlace)

    @pytest.mark.needsdocstring
    def test_unrecognized_place(self, db_session, create_test_library):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        library = create_test_library(db_session)
        s = SetCoverageAreaScript(_db=db_session)
        for arg in ['service-area', 'focus-area']:
            args = ["--library=%s" % library.name, '--%s={"US": "San Francisco"}' % arg]

            with pytest.raises(ValueError) as exc:
                s.run(args, place_class=MockPlace)

            assert "Unknown places:" in str(exc.value)

    @pytest.mark.needsdocstring
    def test_ambiguous_place(self, db_session, create_test_library):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        MockPlace.by_name["OO"] = MockPlace.AMBIGUOUS

        library = create_test_library(db_session)
        s = SetCoverageAreaScript(_db=db_session)
        for arg in ['service-area', 'focus-area']:
            args = ["--library=%s" % library.name, '--%s={"OO": "everywhere"}' % arg]
            with pytest.raises(ValueError) as exc:
                s.run(args, place_class=MockPlace)
            assert "Ambiguous places:" in str(exc.value)
        MockPlace.by_name = {}

    @pytest.mark.needsdocstring
    def test_success(self, db_session, create_test_library, create_test_place):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        us = create_test_place(db_session, place_type=Place.NATION, abbreviated_name='US')
        library = create_test_library(db_session)
        s = SetCoverageAreaScript(_db=db_session)

        # Setting an eligibility area with no focus area assigns that service area to the library.
        args = ["--library=%s" % library.name, '--service-area={"US": "everywhere"}']
        s.run(args)
        [area] = library.service_areas
        assert area.place == us
        assert area.type == ServiceArea.FOCUS

        # Try again, setting both eligibility area (called "service area" here) and focus area.

        # Note that running this script a second time replaces the old service areas rather than adding to them.
        uk = create_test_place(db_session, place_type=Place.NATION, abbreviated_name='UK')
        args = ["--library=%s" % library.name, '--focus-area={"UK": "everywhere"}', '--service-area="everywhere"']
        s.run(args)
        [focus] = [x.place for x in library.service_areas if x.type == ServiceArea.FOCUS]
        [eligibility] = [x.place for x in library.service_areas if x.type == ServiceArea.ELIGIBILITY]
        assert uk == focus
        assert eligibility.type == Place.EVERYWHERE

        # If a default nation is set, you can name a single place as your service area.
        ConfigurationSetting.sitewide(db_session, Configuration.DEFAULT_NATION_ABBREVIATION).value = "US"
        ut = create_test_place(db_session, place_type=Place.STATE, abbreviated_name='UT', parent=us)

        args = ["--library=%s" % library.name, '--service-area=UT']
        s.run(args)

        # Again, running the script completely overwrites your service areas.
        [area] = library.service_areas
        assert area.place == ut

        for setting_obj in db_session.query(ConfigurationSetting).all():
            db_session.delete(setting_obj)

        for place_obj in db_session.query(Place).all():
            db_session.delete(place_obj)

        db_session.commit()


class TestConfigureEmailerScript:
    @pytest.mark.needsdocstring
    def test_run(self, db_session):
        """
        GIVEN:
        WHEN:
        THEN:
        """
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
        script = ConfigureEmailerScript(db_session)
        script.do_run(db_session, cmd_args=cmd_args, emailer_class=Mock)

        # The ExternalIntegration is properly configured.
        emailer = Emailer._sitewide_integration(db_session)
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

        for setting_obj in db_session.query(ConfigurationSetting).all():
            db_session.delete(setting_obj)

        for integration_obj in db_session.query(ExternalIntegration).all():
            db_session.delete(integration_obj)

        db_session.commit()


class TestConfigureVendorIDScript:
    @pytest.mark.needsdocstring
    def test_run(self, db_session):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        cmd_args = [
            "--vendor-id=LIBR",
            "--node-value=abc12",
            "--delegate=http://server1/AdobeAuth/",
            "--delegate=http://server2/AdobeAuth/",
        ]
        script = ConfigureVendorIDScript(db_session)
        script.do_run(db_session, cmd_args=cmd_args)

        # The ExternalIntegration is properly configured.
        integration = ExternalIntegration.lookup(
            db_session, ExternalIntegration.ADOBE_VENDOR_ID, ExternalIntegration.DRM_GOAL
        )
        assert integration.setting(Configuration.ADOBE_VENDOR_ID).value == "LIBR"
        assert integration.setting(Configuration.ADOBE_VENDOR_ID_NODE_VALUE).value == "abc12"
        assert integration.setting(Configuration.ADOBE_VENDOR_ID_DELEGATE_URL).json_value == [
            "http://server1/AdobeAuth/", "http://server2/AdobeAuth/"
        ]

        # The script won't run if --node-value or --delegate have obviously wrong values.
        cmd_args = ["--vendor-id=LIBR", "--node-value=not a hex number"]
        with pytest.raises(ValueError) as exc:
            script.do_run(db_session, cmd_args=cmd_args)
        assert "invalid literal for int" in str(exc.value)

        cmd_args = ["--vendor-id=LIBR", "--node-value=abce", "--delegate=http://random-site/"]
        with pytest.raises(ValueError) as exc:
            script.do_run(db_session, cmd_args=cmd_args)
        assert "Invalid delegate: http://random-site/" in str(exc.value)

        for setting_obj in db_session.query(ConfigurationSetting).all():
            db_session.delete(setting_obj)

        for integration_obj in db_session.query(ExternalIntegration).all():
            db_session.delete(integration_obj)

        db_session.commit()
