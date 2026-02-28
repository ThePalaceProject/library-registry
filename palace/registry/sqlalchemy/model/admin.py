"""Admin model for admin users."""

from __future__ import annotations

from flask_bcrypt import check_password_hash, generate_password_hash
from sqlalchemy import Column, Integer, Unicode

from .base import Base, create, get_one


class Admin(Base):
    __tablename__ = "admins"
    id = Column(Integer, primary_key=True)
    username = Column(Unicode, index=True, unique=True, nullable=False)
    password = Column(Unicode, index=True)

    @classmethod
    def make_password(cls, raw_password):
        return generate_password_hash(raw_password).decode("utf-8")

    def check_password(self, raw_password):
        return check_password_hash(self.password, raw_password)

    @classmethod
    def authenticate(cls, _db, username, password):
        """Finds an authenticated Admin by username and password
        :return: Admin or None
        """
        setting_up = _db.query(Admin).count() == 0

        if setting_up:
            admin, ignore = create(_db, Admin, username=username)
            admin.password = cls.make_password(password)
            return admin
        else:
            admin: Admin = get_one(_db, Admin, username=username)
            if admin and admin.check_password(password):
                return admin

        return None

    def __repr__(self):
        return "<Admin: username=%s>" % self.username
