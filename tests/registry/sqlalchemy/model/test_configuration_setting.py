import pytest
from sqlalchemy.exc import IntegrityError

from palace.registry.sqlalchemy.model.configuration_setting import ConfigurationSetting
from palace.registry.sqlalchemy.model.external_integration import ExternalIntegration
from palace.registry.sqlalchemy.util import create
from tests.fixtures.database import DatabaseTransactionFixture


class TestConfigurationSetting:
    def test_is_secret(self, db: DatabaseTransactionFixture):
        """Some configuration settings are considered secrets,
        and some are not.
        """
        m = ConfigurationSetting._is_secret
        assert m("secret") is True
        assert m("password") is True
        assert m("its_a_secret_to_everybody") is True
        assert m("the_password") is True
        assert m("password_for_the_account") is True
        assert m("public_information") is False

        assert ConfigurationSetting.sitewide(db.session, "secret_key").is_secret is True
        assert (
            ConfigurationSetting.sitewide(db.session, "public_key").is_secret is False
        )

    def test_value_or_default(self, db: DatabaseTransactionFixture):
        integration, ignore = create(
            db.session,
            ExternalIntegration,
            goal=db.fresh_str(),
            protocol=db.fresh_str(),
        )
        setting = integration.setting("key")
        assert setting.value is None

        # If the setting has no value, value_or_default sets the value to
        # the default, and returns the default.
        assert setting.value_or_default("default value") == "default value"
        assert setting.value == "default value"

        # Once the value is set, value_or_default returns the value.
        assert setting.value_or_default("new default") == "default value"

        # If the setting has any value at all, even the empty string,
        # it's returned instead of the default.
        setting.value = ""
        assert setting.value_or_default("default") == ""

    def test_value_inheritance(self, db: DatabaseTransactionFixture):

        key = "SomeKey"

        # Here's a sitewide configuration setting.
        sitewide_conf = ConfigurationSetting.sitewide(db.session, key)

        # Its value is not set.
        assert sitewide_conf.value is None

        # Set it.
        sitewide_conf.value = "Sitewide value"
        assert sitewide_conf.value == "Sitewide value"

        # Here's an integration, let's say the Adobe Vendor ID setup.
        adobe, ignore = create(
            db.session,
            ExternalIntegration,
            goal=ExternalIntegration.DRM_GOAL,
            protocol="Adobe Vendor ID",
        )

        # It happens to a ConfigurationSetting for the same key used
        # in the sitewide configuration.
        adobe_conf = ConfigurationSetting.for_externalintegration(key, adobe)

        # But because the meaning of a configuration key differ so
        # widely across integrations, the Adobe integration does not
        # inherit the sitewide value for the key.
        assert adobe_conf.value is None
        adobe_conf.value = "Adobe value"

        # Here's a library which has a ConfigurationSetting for the same
        # key used in the sitewide configuration.
        library = db.library()
        library_conf = ConfigurationSetting.for_library(key, library)

        # Since all libraries use a given ConfigurationSetting to mean
        # the same thing, a library _does_ inherit the sitewide value
        # for a configuration setting.
        assert library_conf.value == "Sitewide value"

        # Change the site-wide configuration, and the default also changes.
        sitewide_conf.value = "New site-wide value"
        assert library_conf.value == "New site-wide value"

        # The per-library value takes precedence over the site-wide
        # value.
        library_conf.value = "Per-library value"
        assert library_conf.value == "Per-library value"

        # Now let's consider a setting like on the combination of a library and an
        # integration integration.
        key = "patron_identifier_prefix"
        library_patron_prefix_conf = (
            ConfigurationSetting.for_library_and_externalintegration(
                db.session, key, library, adobe
            )
        )
        assert library_patron_prefix_conf.value is None

        # If the integration has a value set for this
        # ConfigurationSetting, that value is inherited for every
        # individual library that uses the integration.
        generic_patron_prefix_conf = ConfigurationSetting.for_externalintegration(
            key, adobe
        )
        assert generic_patron_prefix_conf.value is None
        generic_patron_prefix_conf.value = "Integration-specific value"
        assert library_patron_prefix_conf.value == "Integration-specific value"

        # Change the value on the integration, and the default changes
        # for each individual library.
        generic_patron_prefix_conf.value = "New integration-specific value"
        assert library_patron_prefix_conf.value == "New integration-specific value"

        # The library+integration setting takes precedence over the
        # integration setting.
        library_patron_prefix_conf.value = "Library-specific value"
        assert library_patron_prefix_conf.value == "Library-specific value"

    def test_duplicate(self, db: DatabaseTransactionFixture):
        """You can't have two ConfigurationSettings for the same key,
        library, and external integration.

        (test_relationships shows that you can have two settings for the same
        key as long as library or integration is different.)
        """
        key = db.fresh_str()
        integration, ignore = create(
            db.session,
            ExternalIntegration,
            goal=db.fresh_str(),
            protocol=db.fresh_str(),
        )
        library = db.library()
        setting = ConfigurationSetting.for_library_and_externalintegration(
            db.session, key, library, integration
        )
        setting2 = ConfigurationSetting.for_library_and_externalintegration(
            db.session, key, library, integration
        )
        assert setting2 == setting
        with pytest.raises(IntegrityError):
            create(
                db.session,
                ConfigurationSetting,
                key=key,
                library_id=library.id,
                external_integration=integration,
            )
        # We really screwed up the database session there -- roll it back
        # so that test cleanup can proceed.
        db.session.rollback()

    def test_int_value(self, db: DatabaseTransactionFixture):
        number = ConfigurationSetting.sitewide(db.session, "number")
        assert number.int_value is None

        number.value = "1234"
        assert number.int_value == 1234

        number.value = "tra la la"
        with pytest.raises(ValueError):
            number.int_value

    def test_float_value(self, db: DatabaseTransactionFixture):
        number = ConfigurationSetting.sitewide(db.session, "number")
        assert number.int_value is None

        number.value = "1234.5"
        assert number.float_value == 1234.5

        number.value = "tra la la"
        with pytest.raises(ValueError):
            number.float_value

    def test_json_value(self, db: DatabaseTransactionFixture):
        jsondata = ConfigurationSetting.sitewide(db.session, "json")
        assert jsondata.int_value is None

        jsondata.value = "[1,2]"
        assert jsondata.json_value == [1, 2]

        jsondata.value = "tra la la"
        with pytest.raises(ValueError):
            jsondata.json_value

    def test_explain(self, db: DatabaseTransactionFixture):
        integration, ignore = create(
            db.session, ExternalIntegration, protocol="protocol", goal="goal"
        )
        integration.name = "The Integration"
        integration.setting("somesetting").value = "somevalue"
        integration.setting("password").value = "somepass"

        expect = (
            "ID: %s\n"
            "Name: The Integration\n"
            "Protocol/Goal: protocol/goal\n"
            "somesetting='somevalue'"
        )
        actual = integration.explain()
        assert "\n".join(actual) == expect % integration.id

        # If we pass in True for include_secrets, we see the passwords.
        with_secrets = integration.explain(include_secrets=True)
        assert "password='somepass'" in with_secrets
