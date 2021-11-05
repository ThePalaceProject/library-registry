"""
Tests for the DelegatedPatronIdentifier model.
"""
import uuid

import pytest

from library_registry.model import DelegatedPatronIdentifier


class TestDelegatedPatronIdentifierModel:
    def test_get_one_or_create_new_instance(self, db_session, create_test_library):
        """
        GIVEN: A Library instance, a string representing a patron identifer which does not exist
               in the database, and a callable that returns an id value
        WHEN:  Those values are passed to DelegatedPatronIdentifier.get_one_or_create()
        THEN:  A new DelegatedPatronIdentifier instance affiliated with the Library instance
               should be returned
        """
        library = create_test_library(db_session)
        patron_identifier = str(uuid.uuid4())
        identifier_type = DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID
        mock_id = "patron_id_1"

        def make_id():
            return mock_id

        (identifier, is_new) = DelegatedPatronIdentifier.get_one_or_create(
            db_session, library, patron_identifier, identifier_type, make_id
        )

        assert is_new is True
        assert identifier.library == library
        assert identifier.patron_identifier == patron_identifier
        assert identifier.delegated_identifier == mock_id

        db_session.delete(library)
        db_session.delete(identifier)
        db_session.commit()

    def test_get_one_or_create_existing_instance(self, db_session, create_test_library):
        """
        GIVEN: A Library instance, a string representing an existing patron id, and two callables
               capable of returning an id string
        WHEN:  DelegatedPatronIdentifier.get_one_or_create() is called on the same patron id and library
               more than once, with a new id making callable in the second call
        THEN:  A DelegatedPatronIdentifier instance representing the existing patron should be
               returned, and the second id maker callable should not be used.
        """
        library = create_test_library(db_session)
        patron_identifier = str(uuid.uuid4())
        identifier_type = DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID
        mock_id = "patron_id_1"

        def make_id():
            return mock_id

        (identifier1, id_1_is_new) = DelegatedPatronIdentifier.get_one_or_create(
            db_session, library, patron_identifier, identifier_type, make_id
        )

        assert id_1_is_new is True

        def make_id_other():
            return "SHOULD NOT BE USED"

        (identifier2, id_2_is_new) = DelegatedPatronIdentifier.get_one_or_create(
            db_session, library, patron_identifier, identifier_type, make_id_other
        )

        assert identifier1.id == identifier2.id
        assert id_2_is_new is False
        assert identifier2.delegated_identifier == mock_id

        db_session.delete(library)
        db_session.delete(identifier1)
        db_session.commit()

    def test_get_one_or_create_bad_create_function(self, db_session, create_test_library):
        """
        GIVEN: A callable that raises an exception
        WHEN:  That callable is passed to DelegatedPatronIdentifier with info for a new
               patron, which does not exist in the databsae
        THEN:  The callable's exception should be raised
        """
        library = create_test_library(db_session)
        patron_identifier = str(uuid.uuid4())
        identifier_type = DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID
        exc_msg = "A bad thing happened."

        def bad_make_id():
            raise Exception(exc_msg)

        with pytest.raises(Exception) as exc:
            (identifier, is_new) = DelegatedPatronIdentifier.get_one_or_create(
                db_session, library, patron_identifier, identifier_type, bad_make_id
            )
        assert exc_msg in str(exc.value)

        db_session.delete(library)
        db_session.commit()
