import pytest

from palace.registry.emailer import Emailer
from palace.registry.sqlalchemy.model.base import Base
from tests.testing import DatabaseTest


@pytest.fixture(scope="session", autouse=True)
def teardown_all_tables_after_test_session():
    """Drop all tables after running the tests
    This is required when tables get new columns"""
    # yield and run the tests
    yield
    engine, connection = DatabaseTest.get_database_connection()
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def no_recipient_override(monkeypatch: pytest.MonkeyPatch):
    """Keep a developer's EMAILER_RECIPIENT_OVERRIDE out of every test."""
    monkeypatch.delenv(Emailer.ENV_RECIPIENT_OVERRIDE_ADDRESS, raising=False)


pytest_plugins = [
    "tests.fixtures.database",
    "tests.fixtures.controller",
    "tests.fixtures.s3",
]
