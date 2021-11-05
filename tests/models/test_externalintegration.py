"""
Tests for the ExternalIntegration model.
"""
import pytest       # noqa: F401

from library_registry.model import ConfigurationSetting, ExternalIntegration


class TestExternalIntegrationModel:
    def test_setting(self, db_session, create_test_external_integration):
        """
        GIVEN: An ExternalIntegration object and a configuration key name
        WHEN:  .setting(keyname) is called on that object
        THEN:  A ConfigurationSetting object should be found or created for that keyname,
               associated with the ExternalIntegration object
        """
        integration = create_test_external_integration(db_session)
        keyname = "test_setting"
        assert db_session.query(ConfigurationSetting).count() == 0      # No settings exist yet
        created_setting = integration.setting(keyname)
        assert db_session.query(ConfigurationSetting).count() == 1      # We've created one
        found_setting = integration.setting(keyname)
        assert db_session.query(ConfigurationSetting).count() == 1      # We've found but not created
        assert created_setting == found_setting

        db_session.delete(created_setting)
        db_session.delete(integration)
        db_session.commit()

    def test_set_setting(self, db_session, create_test_external_integration):
        """
        GIVEN: An ExternalIntegration object and a configuration key name
        WHEN:  .set_setting() is called on that object and key name, with a value
        THEN:  The associated ConfigurationSetting object's value should be updated
        """
        integration = create_test_external_integration(db_session)
        keyname = "test_set_setting"
        value_one = "alpha"
        value_two = "bravo"
        assert db_session.query(ConfigurationSetting).count() == 0      # No settings exist yet
        setting = integration.setting(keyname)
        assert db_session.query(ConfigurationSetting).count() == 1      # Created one setting
        assert setting.value is None                                    # It currently has no value
        integration.set_setting(keyname, value_one)
        assert setting.value == value_one                               # Set it to the first value
        integration.set_setting(keyname, value_two)
        assert setting.value == value_two                               # Set it to the second value

        db_session.delete(setting)
        db_session.delete(integration)
        db_session.commit()

    def test_explain(self, db_session, create_test_external_integration):
        """
        GIVEN: An ExternalIntegration object with zero or more associated ConfigurationSetting objects
        WHEN:  .explain() is called on that ExternalIntegration
        THEN:  An array of text lines should be returned, explaining the ExternalIntegration's settings
        """
        integration_protocol = "test protocol"
        integration_goal = ExternalIntegration.LOGGING_GOAL
        integration_name = "Explain Test Integration"
        integration = create_test_external_integration(
            db_session,
            protocol=integration_protocol,
            goal=integration_goal,
            name=integration_name
        )

        (key_one, value_one, key_two, value_two) = ("key_one", "value_one", "key_two", "value_two")
        (secret_key_one, secret_value_one) = ("secret_key_one", "secret_value_one")
        integration.set_setting(key_one, value_one)
        integration.set_setting(key_two, value_two)
        integration.set_setting(secret_key_one, secret_value_one)

        # Get an explanation without secrets and check it for the right parts
        lines_without_secrets = integration.explain(include_secrets=False)
        assert lines_without_secrets[0].startswith("ID: ")
        assert lines_without_secrets[1] == f"Name: {integration_name}"
        assert lines_without_secrets[2] == f"Protocol/Goal: {integration_protocol}/{ExternalIntegration.LOGGING_GOAL}"
        assert sorted(lines_without_secrets[3:]) == sorted([f"{key_one}='{value_one}'", f"{key_two}='{value_two}'"])

        # Make sure no secrets came with it
        assert not any([x.startswith("secret") for x in lines_without_secrets])

        # Get the same explanation but with secrets, make sure they come through ok
        lines_with_secrets = integration.explain(include_secrets=True)
        assert any([x == f"{secret_key_one}='{secret_value_one}'"] for x in lines_with_secrets)

        db_session.delete(integration)
        db_session.commit()

    def test_lookup(self, db_session, create_test_external_integration):
        """
        GIVEN: An ExternalIntegration protocol and a goal
        WHEN:  ExternalIntegration.lookup() is called on those values
        THEN:  The first matching ExternalIntegration should be returned, or None
        """
        integration_one_name = "test_lookup_IE_alpha"
        integration_two_name = "test_lookup_IE_bravo"
        integration_goal = ExternalIntegration.LOGGING_GOAL
        integration_protocol = "test protocol"

        # Make sure none exist for this goal / protocol combo
        assert db_session.query(ExternalIntegration).count() == 0
        assert ExternalIntegration.lookup(db_session, integration_protocol, integration_goal) is None

        integration_one = create_test_external_integration(
            db_session, protocol=integration_protocol, goal=integration_goal, name=integration_one_name
        )

        # Now one should exist with the values we want
        assert db_session.query(ExternalIntegration).count() == 1
        first_found_integration = ExternalIntegration.lookup(db_session, integration_protocol, integration_goal)
        assert isinstance(first_found_integration, ExternalIntegration)
        assert first_found_integration.goal == integration_goal
        assert first_found_integration.protocol == integration_protocol
        assert first_found_integration.id == integration_one.id

        # Another one, with the same goal and protocol but a new name
        integration_two = create_test_external_integration(
            db_session, protocol=integration_protocol, goal=integration_goal, name=integration_two_name
        )

        assert db_session.query(ExternalIntegration).count() == 2
        second_found_integration = ExternalIntegration.lookup(db_session, integration_protocol, integration_goal)

        # Make sure of the two that exist, we get just the first one
        assert second_found_integration.id == integration_one.id

        db_session.delete(integration_one)
        db_session.delete(integration_two)
        db_session.commit()
