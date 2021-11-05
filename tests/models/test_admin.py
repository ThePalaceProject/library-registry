"""
Tests for the Admin model.
"""
import pytest       # noqa: F401
from flask_bcrypt import check_password_hash

from library_registry.model import Admin
from library_registry.model_helpers import create


class TestAdminModel:
    def test_authenticate_autocreates_first_admin(self, db_session):
        """
        GIVEN: A database with no Admin records
        WHEN:  Admin.authenticate() is called with a username and password
        THEN:  A new Admin object should be created and returned
        """
        assert db_session.query(Admin).count() == 0
        username = "testuser"
        password = "testpass"
        admin_obj = Admin.authenticate(db_session, username, password)

        assert isinstance(admin_obj, Admin)
        assert admin_obj

        db_session.delete(admin_obj)
        db_session.commit()

    def test_authenticate_only_autocreates_first_admin(self, db_session):
        """
        GIVEN: A database with at least one Admin record
        WHEN:  Admin.authenticate() is called with a non-existent username/password
        THEN:  No new Admin object should be created
        """
        assert db_session.query(Admin).count() == 0
        username1 = "testuser"
        password1 = "testpass"
        admin_obj_1 = Admin.authenticate(db_session, username1, password1)
        assert db_session.query(Admin).count() == 1

        username2 = "testuser_new"
        password2 = "testpass_new"
        admin_obj_2 = Admin.authenticate(db_session, username2, password2)

        assert admin_obj_2 is None
        assert db_session.query(Admin).count() == 1

        db_session.delete(admin_obj_1)
        db_session.commit()

    def test_check_password(self, db_session):
        """
        GIVEN: An Admin with a password previously set by Admin.make_password()
        WHEN:  Admin.check_password() is called on the plaintext of the password
        THEN:  A boolean should return indicating whether the hashed plaintext
               matches the previously hashed password value stored in the database.
        """
        username = "testuser"
        password = "testpass"
        (admin_obj, _) = create(db_session, Admin, username=username)
        admin_obj.password = Admin.make_password(password)
        assert admin_obj.check_password(password) is True

        db_session.delete(admin_obj)
        db_session.commit()

    def test_make_password(self):
        """
        GIVEN: A plain text password string
        WHEN:  Admin.make_password() is called on that string
        THEN:  A hash value should be returned which can be checked with
               flask_bcrypt.check_password_hash()
        """
        plaintext = "abcdef123$%!"
        hashtext = Admin.make_password(plaintext)
        assert check_password_hash(hashtext, plaintext) is True

    def test_authenticate_success(self, db_session):
        """
        GIVEN: An existing Admin with a password hash set by Admin.make_password()
        WHEN:  Admin.authenticate() is called on that Admin's username and cleartext password
        THEN:  An Admin object representing that record should be returned
        """
        username = "testuser"
        password = "testpass"
        (admin_obj, _) = create(db_session, Admin, username=username)
        admin_obj.password = Admin.make_password(password)
        assert isinstance(admin_obj, Admin)
        assert db_session.query(Admin).count() == 1

        admin_obj_from_authenticate = Admin.authenticate(db_session, username, password)
        assert isinstance(admin_obj_from_authenticate, Admin)
        assert db_session.query(Admin).count() == 1
        assert admin_obj == admin_obj_from_authenticate

        db_session.delete(admin_obj)
        db_session.commit()

    def test_authenticate_failure(self, db_session):
        """
        GIVEN: An existing Admin with a password hash set by Admin.make_password()
        WHEN:  Admin.authenticate() is called with that Admin's username and the wrong password
        THEN:  None should be returned
        """
        username = "testuser"
        password = "testpass"
        (admin_obj, _) = create(db_session, Admin, username=username)
        admin_obj.password = Admin.make_password(password)
        assert isinstance(admin_obj, Admin)

        assert Admin.authenticate(db_session, username, "wrong password") is None

        db_session.delete(admin_obj)
        db_session.commit()
