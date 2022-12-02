import pytest

from model import Base
from testing import DatabaseTest


@pytest.fixture(scope="session", autouse=True)
def teardown_all_tables_after_test_session():
    """Drop all tables after running the tests
    This is required when tables get new columns"""
    # yield and run the tests
    yield
    engine, connection = DatabaseTest.get_database_connection()
    Base.metadata.drop_all(engine)
