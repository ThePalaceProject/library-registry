"""
Tests for the Audience model.
"""
import pytest

from library_registry.model import Audience


class TestAudienceModel:
    def test_lookup_get_or_create(self, db_session):
        """
        GIVEN: An audience name
        WHEN:  Audience.lookup() is called on that name
        THEN:  If the name is present in Audience.KNOWN_AUDIENCES, it should be gotten or created.
        """
        known_audience = Audience.PUBLIC
        assert db_session.query(Audience).count() == 0
        audience_created = Audience.lookup(db_session, known_audience)
        assert db_session.query(Audience).count() == 1
        assert isinstance(audience_created, Audience)
        assert audience_created.name == known_audience

        audience_found = Audience.lookup(db_session, known_audience)
        assert db_session.query(Audience).count() == 1
        assert isinstance(audience_found, Audience)
        assert audience_found.name == known_audience

        db_session.delete(audience_created)
        db_session.commit()

    def test_lookup_unknown_audience(self, db_session):
        """
        GIVEN: An audience name
        WHEN:  Audience.lookup() is called on that name
        THEN:  If the name is not present in Audience.KNOWN_AUDIENCES, a ValueError should be raised
        """
        unknown_audience = "somebody-we-dont-know"
        assert db_session.query(Audience).count() == 0

        with pytest.raises(ValueError):
            Audience.lookup(db_session, unknown_audience)

        assert db_session.query(Audience).count() == 0
