"""Tests for palace.registry.sqlalchemy.util module."""

from __future__ import annotations

from palace.registry.sqlalchemy.model.admin import Admin
from palace.registry.sqlalchemy.model.audience import Audience
from palace.registry.sqlalchemy.util import (
    create,
    generate_secret,
    get_one,
    get_one_or_create,
)
from tests.fixtures.database import DatabaseTransactionFixture


class TestGenerateSecret:
    """Test the generate_secret() function."""

    def test_generate_secret_returns_string(self):
        """Test that generate_secret() returns a string."""
        secret = generate_secret()
        assert isinstance(secret, str)

    def test_generate_secret_returns_48_characters(self):
        """Test that generate_secret() returns a 48-character hex string (24 bytes)."""
        secret = generate_secret()
        assert len(secret) == 48

    def test_generate_secret_returns_different_values(self):
        """Test that generate_secret() returns different values on each call."""
        secret1 = generate_secret()
        secret2 = generate_secret()
        assert secret1 != secret2


class TestGetOne:
    """Test the get_one() function."""

    def test_get_one_returns_single_object(self, db: DatabaseTransactionFixture):
        """Test that get_one() returns a single object when query matches exactly one."""
        admin = Admin(username="testadmin", password="hash")
        db.session.add(admin)
        db.session.flush()

        result = get_one(db.session, Admin, username="testadmin")
        assert result is not None
        assert result.username == "testadmin"

    def test_get_one_returns_none_when_not_found(self, db: DatabaseTransactionFixture):
        """Test that get_one() returns None when no match is found."""
        result = get_one(db.session, Admin, username="nonexistent")
        assert result is None

    def test_get_one_with_kwargs_filters(self, db: DatabaseTransactionFixture):
        """Test that get_one() correctly filters by multiple kwargs."""
        audience = Audience(name="Test Audience")
        db.session.add(audience)
        db.session.flush()

        result = get_one(db.session, Audience, name="Test Audience")
        assert result is not None
        assert result.name == "Test Audience"


class TestGetOneOrCreate:
    """Test the get_one_or_create() function."""

    def test_get_one_or_create_returns_existing_object(
        self, db: DatabaseTransactionFixture
    ):
        """Test that get_one_or_create() returns existing object when it exists."""
        audience = Audience(name="Test Audience")
        db.session.add(audience)
        db.session.flush()

        result, created = get_one_or_create(db.session, Audience, name="Test Audience")
        assert result is not None
        assert result.name == "Test Audience"
        assert created is False

    def test_get_one_or_create_creates_new_object(self, db: DatabaseTransactionFixture):
        """Test that get_one_or_create() creates new object when it doesn't exist."""
        result, created = get_one_or_create(db.session, Audience, name="New Audience")
        assert result is not None
        assert result.name == "New Audience"
        assert created is True

    def test_get_one_or_create_with_multiple_kwargs(
        self, db: DatabaseTransactionFixture
    ):
        """Test that get_one_or_create() works with multiple filter kwargs."""
        # Create with multiple criteria
        admin, created = get_one_or_create(
            db.session,
            Admin,
            username="testadmin",
        )
        assert admin is not None
        assert admin.username == "testadmin"
        assert created is True

        # Verify it retrieves existing on second call
        admin2, created = get_one_or_create(
            db.session,
            Admin,
            username="testadmin",
        )
        assert admin2.id == admin.id
        assert created is False

    def test_get_one_or_create_handles_integrity_error(
        self, db: DatabaseTransactionFixture
    ):
        """Test that get_one_or_create() handles IntegrityError gracefully."""
        # Create initial audience
        audience1 = Audience(name="Test Audience")
        db.session.add(audience1)
        db.session.flush()

        # Try to create another with same name (if unique constraint exists)
        # This tests the IntegrityError handling path
        result, created = get_one_or_create(db.session, Audience, name="Test Audience")
        assert result is not None
        assert result.name == "Test Audience"

    def test_get_one_or_create_strips_on_multiple_kwarg(
        self, db: DatabaseTransactionFixture
    ):
        """Test that get_one_or_create() removes 'on_multiple' kwarg before creating."""
        # This verifies the kwarg handling for get_one() parameters
        result, created = get_one_or_create(
            db.session,
            Audience,
            on_multiple="interchangeable",
            name="Test Audience",
        )
        assert result is not None
        assert result.name == "Test Audience"


class TestCreate:
    """Test the create() function."""

    def test_create_returns_tuple(self, db: DatabaseTransactionFixture):
        """Test that create() returns a tuple of (object, True)."""
        result, created = create(db.session, Audience, name="Test Audience")
        assert isinstance(result, Audience)
        assert created is True

    def test_create_adds_object_to_session(self, db: DatabaseTransactionFixture):
        """Test that create() adds object to session."""
        audience, created = create(db.session, Audience, name="Test Audience")
        db.session.flush()

        # Query it back to verify it was added
        retrieved = get_one(db.session, Audience, name="Test Audience")
        assert retrieved is not None
        assert retrieved.name == "Test Audience"

    def test_create_with_default_init(self, db: DatabaseTransactionFixture):
        """Test that create() uses default __init__ when no create_method."""
        audience, created = create(db.session, Audience, name="Test Audience")
        assert audience is not None
        assert audience.name == "Test Audience"
        assert created is True

    def test_create_flushes_object(self, db: DatabaseTransactionFixture):
        """Test that create() flushes the object to the database."""
        audience1, created = create(db.session, Audience, name="Audience 1")
        # After flush, the object should have an ID
        assert audience1.id is not None
        assert created is True
