from palace.registry.sqlalchemy.model.delegated_patron_identifier import (
    DelegatedPatronIdentifier,
)
from tests.fixtures.database import DatabaseTransactionFixture


class TestDelegatedPatronIdentifier:
    def test_get_one_or_create(self, db: DatabaseTransactionFixture):
        library = db.library()
        patron_identifier = db.fresh_str()
        identifier_type = DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID

        def make_id():
            return "id1"

        identifier, is_new = DelegatedPatronIdentifier.get_one_or_create(
            db.session, library, patron_identifier, identifier_type, make_id
        )
        assert is_new is True
        assert identifier.library == library
        assert identifier.patron_identifier == patron_identifier
        # id_1() was called.
        assert identifier.delegated_identifier == "id1"

        # Try the same thing again but provide a different create_function
        # that raises an exception if called.
        def explode():
            raise Exception("I should never be called.")

        identifier2, is_new = DelegatedPatronIdentifier.get_one_or_create(
            db.session, library, patron_identifier, identifier_type, explode
        )
        # The existing identifier was looked up.
        assert is_new is False
        assert identifier.id == identifier2.id
        # id_2() was not called.
        assert identifier2.delegated_identifier == "id1"
