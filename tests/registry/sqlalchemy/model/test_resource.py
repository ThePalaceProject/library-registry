import datetime

import pytest

from palace.registry.sqlalchemy.model.resource import Validation
from palace.registry.sqlalchemy.util import create
from palace.registry.util.datetime_helpers import utc_now
from tests.fixtures.database import DatabaseTransactionFixture


class TestValidation:
    """Test the Resource validation process."""

    def test_restart_validation(self, db: DatabaseTransactionFixture):

        # This library has two links.
        library = db.library()
        link1, ignore = library.set_hyperlink("rel", "mailto:me@library.org")
        email = link1.resource
        link2, ignore = library.set_hyperlink("rel", "http://library.org")
        http = link2.resource

        # Let's set up validation for both of them.
        now = utc_now()
        email_validation = email.restart_validation()
        http_validation = http.restart_validation()

        for v in (email_validation, http_validation):
            assert (v.started_at - now).total_seconds() < 2
            assert v.secret is not None

        # A random secret was generated for each Validation.
        assert email_validation.secret != http_validation.secret

        # Let's imagine that validation succeeded and is being
        # invalidated for some reason.
        email_validation.success = True
        old_secret = email_validation.secret
        email_validation_2 = email.restart_validation()

        # Instead of a new Validation being created, the earlier
        # Validation has been invalidated.
        assert email_validation_2 == email_validation
        assert email_validation_2.success is False

        # The secret has changed.
        assert old_secret != email_validation.secret

    def test_mark_as_successful(self, db: DatabaseTransactionFixture):

        validation, ignore = create(db.session, Validation)
        assert validation.active is True
        assert validation.success is False
        assert validation.secret is not None

        validation.mark_as_successful()
        assert validation.active is False
        assert validation.success is True
        assert validation.secret is None

        # A validation that has already succeeded cannot be marked
        # as successful.
        with pytest.raises(Exception) as exc:
            validation.mark_as_successful()
        assert "This validation has already succeeded" in str(exc.value)

        # A validation that has expired cannot be marked as successful.
        validation.restart()
        validation.started_at = utc_now() - datetime.timedelta(days=7)
        assert validation.active is False
        with pytest.raises(Exception) as exc:
            validation.mark_as_successful()
        assert "This validation has expired" in str(exc.value)
