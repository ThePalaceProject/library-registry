from palace.registry.sqlalchemy.model.admin import Admin
from tests.fixtures.database import DatabaseTransactionFixture


class TestAdmin:
    def test_make_password(self, db: DatabaseTransactionFixture):
        self.admin = db.admin()
        assert self.admin.password.startswith("$2b$")

    def test_check_password(self, db: DatabaseTransactionFixture):
        self.admin = db.admin()
        assert self.admin.check_password("123")
        assert not self.admin.check_password("wrong")

    def test_authenticate(self, db: DatabaseTransactionFixture):
        self.admin = db.admin()
        # Successfully authenticate existing admin
        assert Admin.authenticate(db.session, "Admin", "123") == self.admin
        # Unsuccessfully authenticate existing admin
        assert Admin.authenticate(db.session, "Admin", "wrong") is None

    def test_authenticate_and_verify_no_new_admins_were_created(
        self, db: DatabaseTransactionFixture
    ):
        self.admin = db.admin()
        assert Admin.authenticate(db.session, "Admin", "123") == self.admin
        before_count = db.session.query(Admin).count()
        assert Admin.authenticate(db.session, "any_username", "any_password") is None
        after_count = db.session.query(Admin).count()
        assert before_count == after_count

    def test_make_new_admin(self, db: DatabaseTransactionFixture):
        self.admin = db.admin()
        # Create the first admin
        db.session.delete(self.admin)
        new_admin = Admin.authenticate(db.session, "New", "password")
        assert new_admin.username == "New"
        assert new_admin.password.startswith("$2b$")
        # Now that there's an admin, subsequent attempts to make a new admin won't work.
        another_admin = Admin.authenticate(db.session, "Another", "password")
        assert another_admin is None
