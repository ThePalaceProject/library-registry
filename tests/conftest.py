import pytest


@pytest.fixture
def flask_client():
    ...


@pytest.fixture
def test_db():
    ...


@pytest.fixture(scope='session')
def simple_library():
    ...
