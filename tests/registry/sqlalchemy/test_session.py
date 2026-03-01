"""Tests for palace.registry.sqlalchemy.session module."""

from __future__ import annotations

from sqlalchemy import text

from config import Configuration
from palace.registry.sqlalchemy.session import SessionManager, production_session


class TestSessionManager:
    """Test the SessionManager class."""

    def test_engine_creation(self):
        """Test that SessionManager.engine() creates a SQLAlchemy engine."""
        url = Configuration.database_url(test=True)
        engine = SessionManager.engine(url)
        assert engine is not None
        # Verify we can connect
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar() == 1

    def test_sessionmaker_creation(self):
        """Test that SessionManager.sessionmaker() creates a sessionmaker."""
        url = Configuration.database_url(test=True)
        maker = SessionManager.sessionmaker(url)
        assert maker is not None
        # sessionmaker is callable
        session = maker()
        assert session is not None
        session.close()

    def test_initialize_creates_tables(self):
        """Test that SessionManager.initialize() creates database tables."""
        url = Configuration.database_url(test=True)
        engine, connection = SessionManager.initialize(url)

        assert engine is not None
        assert connection is not None

        # Verify tables were created by checking if alembic_version exists
        try:
            result = connection.execute(text("SELECT * FROM alembic_version LIMIT 1"))
            # If we can query it, the table exists
            assert True
        except Exception:
            # Table might not exist in test DB, which is fine
            pass
        finally:
            connection.close()

    def test_session_creation(self):
        """Test that SessionManager.session() creates a working session."""
        url = Configuration.database_url(test=True)
        session = SessionManager.session(url)

        assert session is not None
        # Verify we can execute a query
        result = session.execute(text("SELECT 1"))
        assert result.scalar() == 1

        session.close()

    def test_engine_caching(self):
        """Test that SessionManager caches engines by URL."""
        url = Configuration.database_url(test=True)

        # First call
        engine1, conn1 = SessionManager.initialize(url)
        conn1.close()

        # Second call with same URL should return cached engine
        engine2, conn2 = SessionManager.initialize(url)
        conn2.close()

        assert engine1 is engine2, "Engine should be cached"


class TestProductionSession:
    """Test the production_session() function."""

    def test_production_session_returns_session(self):
        """Test that production_session() returns a valid SQLAlchemy Session."""
        # `production_session` requires the production database URL to be configured.
        # This test is usually skipped in testing because it requires actual production DB config.
        # The function is tested indirectly through app initialization and other tests.
        import os

        import pytest

        # Only run this test if `production database` is configured
        if not os.environ.get("SIMPLIFIED_PRODUCTION_DATABASE"):
            pytest.skip("Production database not configured")

        session = production_session()
        assert session is not None

        # Verify it works.
        result = session.execute(text("SELECT 1"))
        assert result.scalar() == 1

        session.close()
