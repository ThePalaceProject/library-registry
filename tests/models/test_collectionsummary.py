"""
Tests for the CollectionSummary model.
"""
import pytest

from library_registry.model import CollectionSummary


class TestCollectionSummaryModel:
    def test_set(self, db_session, create_test_library):
        """
        GIVEN: A Library instance
        WHEN:  CollectionSummary.set() is called on that library, with a language value and size
        THEN:  A CollectionSummary object should be returned
        """
        library = create_test_library(db_session)
        summary1 = CollectionSummary.set(library, "eng", 100)
        assert summary1.size == 100
        assert summary1.language == "eng"

        summary2 = CollectionSummary.set(library, None, 200)
        assert summary2.size == 200
        assert summary2.language is None

        for db_item in [library, summary1, summary2]:
            db_session.delete(db_item)
        db_session.commit()

    def test_set_unknown_language_set_to_none(self, db_session, create_test_library):
        """
        GIVEN: A Library instance
        WHEN:  CollectionSummary.set() is called on that library, with a language value that does not
               appear in the set of known languages.
        THEN:  The language of the resulting collection should be None
        """
        library = create_test_library(db_session)
        summary = CollectionSummary.set(library, "NOT_A_LANGUAGE", 100)
        assert summary.language is None
        assert summary.size == 100

        db_session.delete(library)
        db_session.delete(summary)
        db_session.commit()

    def test_set_size_must_be_numeric(self, db_session, create_test_library):
        library = create_test_library(db_session)
        with pytest.raises(ValueError) as exc:
            CollectionSummary.set(library, "eng", "NOT_A_NUMBER")
        assert "Collection size must be numeric" in str(exc.value)

        db_session.delete(library)
        db_session.commit()

    def test_set_bad_parameters(self, db_session, create_test_library):
        """
        GIVEN: A Library instance
        WHEN:  CollectionSummary.set() is called with a negative size parameter
        THEN:  An appropriate exception should be raised
        """
        library = create_test_library(db_session)
        with pytest.raises(ValueError) as exc:
            CollectionSummary.set(library, "eng", -100)
        assert "Collection size cannot be negative." in str(exc.value)

        db_session.delete(library)
        db_session.commit()
