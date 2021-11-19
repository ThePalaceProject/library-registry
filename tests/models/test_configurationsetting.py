"""
Tests for the ConfigurationSetting model.
"""
import pytest

import library_registry.model_helpers as model_helpers
from library_registry.model import ConfigurationSetting


class TestConfigurationSettingModel:
    @pytest.mark.parametrize(
        "name,default",
        [
            pytest.param("setting_alpha", "a_string", id="string_default"),
            pytest.param("setting_bravo", 1, id="int_default"),
            pytest.param("setting_charlie", 1.1, id="float_default"),
            pytest.param("setting_delta", [1, 2], id="list_default"),
            pytest.param("setting_echo", {"somekey": "someval"}, id="dict_default"),
            pytest.param("setting_foxtrot", (1, 2, 3), id="tuple_default"),
            pytest.param("setting_golf", True, id="boolean_default"),
            pytest.param("setting_hotel", None, id="none_default"),
            pytest.param("setting_india", "", id="empty_string_default"),
        ]
    )
    def test_setdefault(self, name, default):
        """
        GIVEN: A ConfigurationSetting object which currently has no value
        WHEN:  .setdefault() is called on that object with a default supplied
        THEN:  The default value should become the object's .value, and should be returned
        """
        cs = ConfigurationSetting(key=name)
        assert cs.value is None
        return_value = cs.setdefault(default=default)
        assert return_value == default
        assert cs.value == default
        assert type(cs.value) == type(default)

    @pytest.mark.parametrize(
        "name,result",
        [
            pytest.param("setting_alpha", False, id="public_name"),
            pytest.param("a_secret_thing", True, id="secret_all_lc"),
            pytest.param("A_SECRET_NAME", True, id="secret_all_uc"),
            pytest.param("long_name_with_password_in_the_middle", True, id="secret_long_string"),
            pytest.param("passwordsecret", True, id="concatenated_keywords"),
        ]
    )
    def test__is_secret(self, name, result):
        """
        GIVEN: A string representing a ConfigurationSetting name
        WHEN:  The ConfigurationSetting._is_secret() method is called on that name
        THEN:  A boolean should return, indicating whether the name contains any of
               the strings stored in ConfigurationSetting.SECRET_SETTING_KEYWORDS
        """
        assert ConfigurationSetting._is_secret(name) is result

    def test_value_inheritance_explicitly_set(
        self,
        db_session,
        create_test_library,
        create_test_configuration_setting
    ):
        """
        GIVEN: A ConfigurationSetting object with a ._value attribute that is not None
        WHEN:  The .value property is accessed on that object
        THEN:  The value stored in the ._value attribute should be returned
        """
        library = create_test_library(db_session)
        keyname = "test_value_inheritance_explicitly_set"
        local_value = "a local test value"
        sitewide_value = "the sitewide test value"

        sitewide_setting = ConfigurationSetting.sitewide(db_session, keyname)
        sitewide_setting.value = sitewide_value

        local_setting = create_test_configuration_setting(
            db_session,
            library=library,
            key=keyname,
            value=local_value
        )

        assert sitewide_setting.value == sitewide_value     # Inheritable value exists
        assert local_setting.key == sitewide_setting.key    # Keys match
        assert local_setting.value == local_value           # No inheritance occurs

        db_session.delete(local_setting)
        db_session.delete(sitewide_setting)
        db_session.delete(library)
        db_session.commit()

    def test_value_inheritance_library_specific_integration(
        self,
        db_session,
        create_test_library,
        create_test_external_integration,
        create_test_configuration_setting
    ):
        """
        GIVEN: A ConfigurationSetting object with a ._value of None, associated with both a
               Library and an ExternalIntegration
        WHEN:  The object's .value property is accessed
        THEN:  The value associated with the ExternalIntegration for that key should be returned
        """
        library = create_test_library(db_session)
        integration = create_test_external_integration(db_session)
        keyname = "test_value_inheritance_library_specific_integration"
        local_value = None
        integration_value = "the integration test value"

        integration_setting = create_test_configuration_setting(
            db_session,
            external_integration=integration,
            key=keyname,
            value=integration_value
        )

        local_setting = create_test_configuration_setting(
            db_session,
            library=library,
            external_integration=integration,
            key=keyname,
            value=local_value
        )

        assert local_setting._value is None                         # No explicit value set
        assert integration_setting._value == integration_value      # Inheritable value exists
        assert local_setting.value == integration_setting._value    # Inheritance occurs

        db_session.delete(local_setting)
        db_session.delete(integration_setting)
        db_session.delete(integration)
        db_session.delete(library)
        db_session.commit()

    def test_value_inheritance_library_specific(
        self,
        db_session,
        create_test_library,
        create_test_configuration_setting
    ):
        """
        GIVEN: A ConfigurationSetting object with a ._value of None, associated with a
               Library but not an ExternalIntegration
        WHEN:  The object's .value property is accessed
        THEN:  If a sitewide value exists for the setting key, it should be returned
        """
        library = create_test_library(db_session)
        keyname = "test_value_inheritance_library_specific"
        local_value = None
        sitewide_value = "a sitewide test value"

        sitewide_setting = ConfigurationSetting.sitewide(db_session, keyname)
        sitewide_setting.value = sitewide_value

        local_setting = create_test_configuration_setting(
            db_session,
            library=library,
            key=keyname,
            value=local_value
        )

        assert local_setting._value is None                     # No explicit value set
        assert sitewide_setting._value == sitewide_value        # Inheritable value exists
        assert local_setting.value == sitewide_setting._value   # Inheritance occurs

        db_session.delete(local_setting)
        db_session.delete(sitewide_setting)
        db_session.delete(library)
        db_session.commit()

    def test_uniqueness(self, db_session, create_test_library, create_test_external_integration,
                        create_test_configuration_setting):
        """
        GIVEN: A ConfigurationSetting with a given key, Library, and ExternalIntegration
        WHEN:  An attempt is made to instantiate another ConfigurationSetting using the same
               values for key, Library, and ExternalIntegration
        THEN:  An IntegrityError should be raised

        TODO: Is this test worth having? Feels like it's just testing SQLAlchemy's constraint system.
        """
        shared_library = create_test_library(db_session)
        shared_integration = create_test_external_integration(db_session)
        shared_key = "test_uniqueness"
        shared_value = 1

        cs_original = create_test_configuration_setting(
            db_session,
            library=shared_library,
            external_integration=shared_integration,
            key=shared_key,
            value=shared_value
        )

        assert isinstance(cs_original, ConfigurationSetting)
        assert cs_original.library_id == shared_library.id
        assert cs_original.external_integration_id == shared_integration.id
        assert cs_original.key == shared_key
        assert cs_original.value == str(shared_value)

        cs_copy = create_test_configuration_setting(db_session, library=shared_library,
                                                    external_integration=shared_integration,
                                                    key=shared_key, value=shared_value)

        assert cs_copy.id == cs_original.id

    @pytest.mark.parametrize(
        "value,result",
        [
            pytest.param(1, False, id="int_value"),
            pytest.param(1.1, False, id="float_value"),
            pytest.param([1, 2], False, id="list_value"),
            pytest.param({"somekey": "someval"}, False, id="dict_value"),
            pytest.param((1, 2, 3), False, id="tuple_value"),
            pytest.param(True, True, id="boolean_value_true"),
            pytest.param(False, False, id="boolean_value_false"),
            pytest.param(None, None, id="none_value"),
            pytest.param("", False, id="empty_string_value"),
            pytest.param("TRUE", True, id="string_value_true_uc"),
            pytest.param("before true after", False, id="string_containing_true"),
            pytest.param("t", True, id="string_t"),
            pytest.param("yes", True, id="string_yes"),
            pytest.param("y", True, id="string_y"),
        ]
    )
    def test_bool_value(self, db_session, create_test_configuration_setting, value, result):
        """
        GIVEN: A ConfigurationSetting object
        WHEN:  Its `bool_value` property is accessed
        THEN:  The object's .value attribute should be cast to a Boolean, according to the following:
                 - If the stored value is None, returns None
                 - If the stored value, lower-cased, appears in ConfigurationSetting.MEANS_YES, returns True
                 - If the stored value exists but is not in MEANS_YES, returns False
        """
        setting = create_test_configuration_setting(db_session)
        setting.value = value
        assert setting.bool_value is result
        db_session.delete(setting)
        db_session.commit()

    @pytest.mark.parametrize(
        "value,result",
        [
            pytest.param(1, 1, id="int_value"),
            pytest.param("1", 1, id="string_int_value"),
            pytest.param(1.1, 1, id="float_value"),
            pytest.param("1.1", 1, id="string_float_value"),
            pytest.param([1], None, id="list_value"),
            pytest.param({"somekey": 1}, None, id="dict_value"),
            pytest.param(None, None, id="none_value"),
            pytest.param(True, None, id="boolean_true_value"),
            pytest.param(False, None, id="boolean_false_value"),
            pytest.param("string 1 string", None, id="string_containing_int"),
            pytest.param("string", None, id="string_value"),
        ]
    )
    def test_int_value(self, db_session, create_test_configuration_setting, value, result):
        """
        GIVEN: A ConfigurationSetting object
        WHEN:  Its `int_value` property is accessed
        THEN:  The object's .value attribute should be cast to an integer if possible, else None
        """
        setting = create_test_configuration_setting(db_session)
        setting.value = value
        assert setting.int_value == result
        db_session.delete(setting)
        db_session.commit()

    @pytest.mark.parametrize(
        "value,result",
        [
            pytest.param(1, 1.0, id="int_value"),
            pytest.param("1", 1.0, id="string_int_value"),
            pytest.param(1.1, 1.1, id="float_value"),
            pytest.param("1.1", 1.1, id="string_float_value"),
            pytest.param([1], None, id="list_value"),
            pytest.param({"somekey": 1}, None, id="dict_value"),
            pytest.param(None, None, id="none_value"),
            pytest.param(True, None, id="boolean_true_value"),
            pytest.param(False, None, id="boolean_false_value"),
            pytest.param("string 1 string", None, id="string_containing_int"),
            pytest.param("string", None, id="string_value"),
        ]
    )
    def test_float_value(self, db_session, create_test_configuration_setting, value, result):
        """
        GIVEN: A ConfigurationSetting object
        WHEN:  Its `float_value` property is accessed
        THEN:  The object's .value attribute should be cast to a float if possible, else None
        """
        setting = create_test_configuration_setting(db_session)
        setting.value = value
        assert setting.float_value == result
        db_session.delete(setting)
        db_session.commit()

    @pytest.mark.parametrize(
        "value,result",
        [
            pytest.param(1, None, id="int_value"),
            pytest.param(1.1, None, id="float_value"),
            pytest.param([1], None, id="list_value"),
            pytest.param({"somekey": 1}, None, id="dict_value"),
            pytest.param(None, None, id="none_value"),
            pytest.param(True, None, id="boolean_true_value"),
            pytest.param(False, None, id="boolean_false_value"),
            pytest.param("string", None, id="string_value"),
            pytest.param(
                '{"alpha": 1, "bravo": [2, 3], "charlie": {"delta": "echo"}}',
                {'alpha': 1, 'bravo': [2, 3], 'charlie': {'delta': 'echo'}},
                id="parseable_json_string"
            ),
        ]
    )
    def test_json_value(self, db_session, create_test_configuration_setting, value, result):
        """
        GIVEN: A ConfigurationSetting object
        WHEN:  Its `json_value` property is accessed
        THEN:  An object should be returned if the setting's value can be parsed as valid JSON,
               otherwise None
        """
        setting = create_test_configuration_setting(db_session)
        setting.value = value
        assert setting.json_value == result
        db_session.delete(setting)
        db_session.commit()

    def test_for_library_and_externalintegration(
        self, db_session, create_test_library, create_test_external_integration
    ):
        """
        GIVEN: A Library, an ExternalIntegration, and a key name
        WHEN:  ConfigurationSetting.for_library_and_externalintegration() is called on those values
        THEN:  A ConfigurationSetting object should be found or created, as appropriate
        """
        library = create_test_library(db_session)
        integration = create_test_external_integration(db_session)
        keyname = "test_for_library_and_externalintegration"

        setting_created = ConfigurationSetting.for_library_and_externalintegration(
            db_session,
            key=keyname,
            library=library,
            external_integration=integration
        )

        assert isinstance(setting_created, ConfigurationSetting)
        assert setting_created.library_id == library.id
        assert setting_created.external_integration_id == integration.id
        assert setting_created.key == keyname
        assert db_session.query(ConfigurationSetting).count() == 1

        setting_found = ConfigurationSetting.for_library_and_externalintegration(
            db_session,
            key=keyname,
            library=library,
            external_integration=integration
        )

        assert isinstance(setting_found, ConfigurationSetting)
        assert setting_found == setting_created
        assert db_session.query(ConfigurationSetting).count() == 1

        db_session.delete(setting_created)
        db_session.delete(integration)
        db_session.delete(library)
        db_session.commit()

    def test_for_library(self, db_session, create_test_library):
        """
        GIVEN: A Library and a key name
        WHEN:  ConfigurationSetting.for_library() is called on those values
        THEN:  A ConfigurationSetting object should be found or created, as appropriate
        """
        library = create_test_library(db_session)
        keyname = "test_for_library"

        setting_created = ConfigurationSetting.for_library(keyname, library)

        assert isinstance(setting_created, ConfigurationSetting)
        assert setting_created.library_id == library.id
        assert setting_created.external_integration_id is None
        assert setting_created.key == keyname
        assert db_session.query(ConfigurationSetting).count() == 1

        setting_found = ConfigurationSetting.for_library(keyname, library)

        assert isinstance(setting_found, ConfigurationSetting)
        assert setting_found == setting_created
        assert db_session.query(ConfigurationSetting).count() == 1

        db_session.delete(setting_created)
        db_session.delete(library)
        db_session.commit()

    def test_for_externalintegration(self, db_session, create_test_external_integration):
        """
        GIVEN: An ExternalIntegration and a key name
        WHEN:  ConfigurationSetting.for_externalintegration() is called on those values
        THEN:  A ConfigurationSetting object should be found or created, as appropriate
        """
        integration = create_test_external_integration(db_session)
        keyname = "test_for_externalintegration"

        setting_created = ConfigurationSetting.for_externalintegration(keyname, integration)

        assert isinstance(setting_created, ConfigurationSetting)
        assert setting_created.library_id is None
        assert setting_created.external_integration_id == integration.id
        assert setting_created.key == keyname
        assert db_session.query(ConfigurationSetting).count() == 1

        setting_found = ConfigurationSetting.for_externalintegration(keyname, integration)

        assert isinstance(setting_found, ConfigurationSetting)
        assert setting_found == setting_created
        assert db_session.query(ConfigurationSetting).count() == 1

        db_session.delete(setting_created)
        db_session.delete(integration)
        db_session.commit()

    def test_sitewide(self, db_session):
        """
        GIVEN: A key name
        WHEN:  ConfigurationSetting.sitewide() is called on that key name
        THEN:  A ConfigurationSetting object should be found or created, associated with that key name
        """
        assert db_session.query(ConfigurationSetting).count() == 0              # No settings exist yet
        keyname = "test_sitewide"
        created_setting = ConfigurationSetting.sitewide(db_session, keyname)
        assert db_session.query(ConfigurationSetting).count() == 1              # First call creates a setting
        assert created_setting.key == keyname

        found_setting = ConfigurationSetting.sitewide(db_session, keyname)
        assert db_session.query(ConfigurationSetting).count() == 1              # Second call finds existing
        assert found_setting.key == keyname

        db_session.delete(created_setting)
        db_session.commit()

    def test_sitewide_secret(self, db_session, monkeypatch):
        """
        GIVEN: A key name
        WHEN:  ConfigurationSetting.sitewide_secret() is called on that key
        THEN:  A ConfigurationSetting object should be found or created for that key. If created,
               or if the current value of the setting does not evaluate to True, then the value
               of the setting object should be set to the output of model_helpers.generate_secret()
        """
        def mock_secret(n=None):
            return "X" * 24

        monkeypatch.setattr(model_helpers, "random_string", mock_secret)

        keyname = "test_sitewide_secret"
        expected = mock_secret()

        assert db_session.query(ConfigurationSetting).count() == 0

        created_setting_value = ConfigurationSetting.sitewide_secret(db_session, keyname)
        assert db_session.query(ConfigurationSetting).count() == 1
        assert created_setting_value == expected

        found_setting_value = ConfigurationSetting.sitewide_secret(db_session, keyname)
        assert found_setting_value == expected
        assert db_session.query(ConfigurationSetting).count() == 1

        created_setting = ConfigurationSetting.sitewide(db_session, keyname)
        db_session.delete(created_setting)
        db_session.commit()
