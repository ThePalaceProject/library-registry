import os
from typing import Any, Callable

import pytest
from flask import Flask

from config import Configuration
from controller import LibraryRegistry
from emailer import Emailer
from model import ConfigurationSetting, ExternalIntegration, get_one_or_create
from testing import DummyHTTPClient
from tests.fixtures.database import DatabaseTransactionFixture


class MockLibraryRegistry(LibraryRegistry):
    pass


class MockEmailer(Emailer):
    @classmethod
    def from_sitewide_integration(cls, _db):
        return cls()

    def __init__(self):
        self.sent_out = []

    def send(self, email_type, to_address, **template_args):
        self.sent_out.append((email_type, to_address, template_args))


class ControllerSetupFixture:
    db: DatabaseTransactionFixture

    def __init__(self, db: DatabaseTransactionFixture):
        self.db = db

    def setup(
        self, do_setup: Callable[["ControllerFixture"], Any] = lambda x: None
    ) -> "ControllerFixture":
        from app import app, set_secret_key

        fixture = ControllerFixture(self)
        ConfigurationSetting.sitewide(
            self.db.session, Configuration.SECRET_KEY
        ).value = "a secret"
        set_secret_key(self.db.session)

        fixture.saved_env = os.environ.get("AUTOINITIALIZE")
        os.environ["AUTOINITIALIZE"] = "False"
        del os.environ["AUTOINITIALIZE"]

        do_setup(fixture)

        fixture.app = app
        fixture.library_registry = MockLibraryRegistry(
            self.db.session, testing=True, emailer_class=MockEmailer
        )
        fixture.app.library_registry = fixture.library_registry
        fixture.http_client = DummyHTTPClient()
        return fixture


class ControllerFixture:
    app: Flask
    library_registry: MockLibraryRegistry
    setup: ControllerSetupFixture
    http_client: DummyHTTPClient
    saved_env: str
    db: DatabaseTransactionFixture

    def __init__(self, setup: ControllerSetupFixture):
        self.setup = setup
        self.db = setup.db

    def vendor_id_setup(self):
        """Configure a basic vendor id service."""
        integration, ignore = get_one_or_create(
            self.setup.db.session,
            ExternalIntegration,
            protocol=ExternalIntegration.ADOBE_VENDOR_ID,
            goal=ExternalIntegration.DRM_GOAL,
        )
        integration.setting(Configuration.ADOBE_VENDOR_ID).value = "VENDORID"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.saved_env is not None:
            os.environ["AUTOINITIALIZE"] = self.saved_env
        return self


@pytest.fixture(scope="function")
def controller_setup_fixture(db: DatabaseTransactionFixture) -> ControllerSetupFixture:
    return ControllerSetupFixture(db)
