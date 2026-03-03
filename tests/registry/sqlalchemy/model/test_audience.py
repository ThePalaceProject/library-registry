import pytest

from palace.registry.sqlalchemy.model.audience import Audience
from tests.fixtures.database import DatabaseTransactionFixture


class TestAudience:
    def test_unrecognized_audience(self, db: DatabaseTransactionFixture):
        with pytest.raises(ValueError) as exc:
            Audience.lookup(db.session, "no such audience")
        assert "Unknown audience: no such audience" in str(exc.value)
