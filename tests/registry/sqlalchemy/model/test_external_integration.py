from palace.registry.sqlalchemy.model.external_integration import ExternalIntegration
from palace.registry.sqlalchemy.util import create
from tests.fixtures.database import DatabaseTransactionFixture


class TestExternalIntegration:
    def test_set_key_value_pair(self, db: DatabaseTransactionFixture):
        """Test the ability to associate extra key-value pairs with
        an ExternalIntegration.
        """
        self.external_integration, ignore = create(
            db.session,
            ExternalIntegration,
            goal=db.fresh_str(),
            protocol=db.fresh_str(),
        )

        assert self.external_integration.settings == []

        setting = self.external_integration.set_setting("website_id", "id1")
        assert setting.key == "website_id"
        assert setting.value == "id1"

        # Calling set() again updates the key-value pair.
        assert self.external_integration.settings == [setting]
        setting2 = self.external_integration.set_setting("website_id", "id2")
        assert setting2 == setting
        assert setting2.value == "id2"

        assert self.external_integration.setting("website_id") == setting2

    def test_explain(self, db: DatabaseTransactionFixture):
        self.external_integration, ignore = create(
            db.session,
            ExternalIntegration,
            goal=db.fresh_str(),
            protocol=db.fresh_str(),
        )

        integration, ignore = create(
            db.session, ExternalIntegration, protocol="protocol", goal="goal"
        )
        integration.name = "The Integration"
        integration.setting("somesetting").value = "somevalue"
        integration.setting("password").value = "somepass"

        expect = """ID: %s
Name: The Integration
Protocol/Goal: protocol/goal
somesetting='somevalue'""" % integration.id
        actual = integration.explain()
        assert expect == "\n".join(actual)

        # If we pass in True for include_secrets, we see the passwords.
        with_secrets = integration.explain(include_secrets=True)
        assert "password='somepass'" in with_secrets
