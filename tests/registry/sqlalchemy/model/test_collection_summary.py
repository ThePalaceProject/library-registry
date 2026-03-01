import pytest

from palace.registry.sqlalchemy.model.collection_summary import CollectionSummary
from tests.fixtures.database import DatabaseTransactionFixture


class TestCollectionSummary:
    def test_set(self, db: DatabaseTransactionFixture):
        library = db.library()
        summary = CollectionSummary.set(library, "eng", 100)
        assert summary.library == library
        assert summary.language == "eng"
        assert summary.size == 100

        # Call set() again and we get the same object back.
        summary2 = CollectionSummary.set(library, "eng", "0")
        assert summary2 == summary
        assert summary.size == 0

    def test_unrecognized_language_is_set_as_unknown(
        self, db: DatabaseTransactionFixture
    ):
        library = db.library()
        summary = CollectionSummary.set(library, "mmmmmm", 100)
        assert summary.language is None
        assert summary.size == 100

    def test_size_must_be_integerable(self, db: DatabaseTransactionFixture):
        library = db.library()
        with pytest.raises(ValueError) as exc:
            CollectionSummary.set(library, "eng", "fruit")
        assert "invalid literal for" in str(exc.value)

    def test_negative_size_is_not_allowed(self, db: DatabaseTransactionFixture):
        library = db.library()
        with pytest.raises(ValueError) as exc:
            CollectionSummary.set(library, "eng", "-1")
        assert "Collection size cannot be negative." in str(exc.value)
