"""
Tests for the Resource, Hyperlink, and Validation models.
"""
from datetime import datetime, timedelta

import pytest

from library_registry.config import Configuration
from library_registry.emailer import Emailer
from library_registry.model import ConfigurationSetting, Hyperlink, Validation
from library_registry.model_helpers import get_one_or_create


class TestResourceModel:
    """
    The Resource model is currently simple enough that it doesn't need unit tests.
    """


class MockEmailer(Emailer):
    def __init__(self):
        """We don't need any of the required args for the real Emailer constructor."""
        self.sent = []
        self.url_for_calls = []

    def send(self, type, to_address, **kwargs):
        self.sent.append((type, to_address, kwargs))
        return self.sent

    def url_for(self, controller, **kwargs):
        self.url_for_calls.append((controller, kwargs))
        return "http://librarysimplified.org/testurl"


@pytest.fixture
def registry_contact_email(db_session):
    setting = ConfigurationSetting.sitewide(
        db_session,
        Configuration.REGISTRY_CONTACT_EMAIL
    )
    setting.value = "me@registry"
    yield setting
    db_session.delete(setting)
    db_session.commit()


class TestHyperlinkModel:
    def test_notify_exit_early(
        self, db_session, create_test_resource, create_test_library, destroy_test_library, registry_contact_email
    ):
        """
        GIVEN: A Hyperlink object
        WHEN:  .notify() is called on that object and any of the following is true:
                 - The emailer passed to the function is invalid
                 - The url_for function passed to the function is invalid
                 - The object does not have an associated Library
                 - The object does not have an associated Resource
        THEN:  The function should exit before doing any work
        """
        library = create_test_library(db_session)
        emailer = MockEmailer()
        resource = create_test_resource(db_session)
        (hyperlink, _) = get_one_or_create(db_session, Hyperlink, rel="TESTREL")

        # If the emailer object passed isn't an Emailer instance, or if the url_for
        # passed isn't a callable, the function should return immediately.
        assert hyperlink.notify(emailer=None, url_for=None) is None
        assert hyperlink.notify(emailer=object(), url_for=object()) is None
        assert hyperlink.notify(emailer=emailer, url_for=object()) is None
        assert not emailer.sent and not emailer.url_for_calls

        # Even with a valid Emailer and url_for, it should still do nothing if the
        # link isn't associated with a Library and a Resource
        assert hyperlink.notify(emailer=emailer, url_for=emailer.url_for) is None
        assert not emailer.sent and not emailer.url_for_calls

        hyperlink.library = library
        assert hyperlink.notify(emailer=emailer, url_for=emailer.url_for) is None
        assert not emailer.sent and not emailer.url_for_calls

        # Now all the pieces are in place, and the call should proceed past the initial
        # early exit checks in notify().
        hyperlink.resource = resource
        hyperlink.notify(emailer=emailer, url_for=emailer.url_for)
        assert emailer.sent and emailer.url_for_calls

        destroy_test_library(db_session, library)
        for db_item in (hyperlink, resource):
            db_session.delete(db_item)
        db_session.commit()

    def test_notify_validated_resource(
        self, db_session, create_test_library, create_test_resource, create_test_validation,
        destroy_test_library, registry_contact_email
    ):
        """
        GIVEN: - A Hyperlink instance which is associated with a Library and a Resource
                 whose href is an email address, and which has been validated already.
               - A valid Emailer and a callable url_for
        WHEN:  The .notify() method is called on the Hyperlink instance
        THEN:  The Emailer's .sent() method should be called, with an email type of
               Emailer.ADDRESS_DESIGNATED.
        """
        emailer = MockEmailer()
        to_address = "serversidetest@librarysimplified.org"
        library = create_test_library(db_session)
        library.web_url = "http://testlibrary"

        (link, is_new) = library.set_hyperlink(
            Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL, to_address
        )
        assert is_new is True

        validation = create_test_validation(
            db_session, link.resource, started_at=(datetime.utcnow() - timedelta(minutes=1))
        )

        assert link.resource.href == to_address
        assert link.resource.validation == validation
        assert link.resource.validation.success is False

        assert emailer.sent == []
        assert emailer.url_for_calls == []

        link.notify(emailer, emailer.url_for)

        assert len(emailer.sent) == 1
        (email_type, email_to, template_vars) = emailer.sent[0]
        assert email_type == Emailer.ADDRESS_DESIGNATED
        assert email_to == to_address
        assert template_vars['email'] == to_address
        assert template_vars['library'] == library.name
        assert template_vars['library_web_url'] == library.web_url
        assert template_vars['registry_support'] == registry_contact_email.value
        assert template_vars['rel_desc'] == Hyperlink.REL_DESCRIPTIONS[Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL]

        # The callable passed to notify() for url_for is only called if the email
        # type is Emailer.ADDRESS_NEEDS_CONFIRMATION, so shouldn't be called this time.
        assert emailer.url_for_calls == []

        destroy_test_library(db_session, library)
        db_session.delete(validation)
        db_session.commit()

    def test_notify_no_validation(
        self, db_session, create_test_library, create_test_resource, create_test_validation,
        destroy_test_library, registry_contact_email
    ):
        """
        GIVEN: - A Hyperlink instance which is associated with a Library and a Resource
                 whose href is an email address, and which does not have an associated
                 Validation instance.
               - A valid Emailer and a a callable url_for
        WHEN:  The .notify() method is called on the Hyperlink instance
        THEN:  The Emailer's .sent() method should be called, with an email type of
               Emailer.ADDRESS_NEEDS_CONFIRMATION.
        """
        emailer = MockEmailer()
        to_address = "serversidetest@librarysimplified.org"
        library = create_test_library(db_session)
        library.web_url = "http://testlibrary"

        (link, is_new) = library.set_hyperlink(Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL, to_address)
        assert is_new is True

        assert emailer.sent == []
        assert emailer.url_for_calls == []

        link.notify(emailer, emailer.url_for)

        assert len(emailer.sent) == 1
        assert len(emailer.url_for_calls) == 1
        (email_type, _, _) = emailer.sent[0]
        assert email_type == Emailer.ADDRESS_NEEDS_CONFIRMATION

        destroy_test_library(db_session, library)


@pytest.fixture(scope="function")
def validation_obj(db_session, create_test_resource, create_test_validation):
    r = create_test_resource(db_session)
    v = create_test_validation(db_session, r)
    yield v
    db_session.delete(v)
    db_session.delete(r)
    db_session.commit()


class TestValidationModel:
    def test_restart_new_validation(self, validation_obj):
        """
        GIVEN: A Validation object
        WHEN:  The .restart() method is called on that object
        THEN:  The validation's started_at time should be reset to now, its secret to a new secret,
               and its success to False
        """
        initial_started_at = validation_obj.started_at
        initial_secret = validation_obj.secret

        validation_obj.restart()

        assert validation_obj.started_at != initial_started_at
        assert validation_obj.secret != initial_secret
        assert validation_obj.success is False

    def test_restart_post_success(self, validation_obj):
        """
        GIVEN: A Validation object which has already been marked as successful
        WHEN:  The .restart() method is called on that object
        THEN:  The validation's started_at time should be reset to now, its secret to a new secret,
               and its success to False
        """
        validation_obj.mark_as_successful()

        initial_started_at = validation_obj.started_at
        initial_secret = validation_obj.secret
        assert validation_obj.success is True

        validation_obj.restart()

        assert validation_obj.started_at != initial_started_at
        assert validation_obj.secret != initial_secret
        assert validation_obj.success is False

    def test_mark_as_successful(self, validation_obj):
        """
        GIVEN: A Validation object which has not been marked successful and has not expired
        WHEN:  .mark_as_successful() is called on that object
        THEN:  The value of .secret should be set to None, and .success to True
        """
        assert validation_obj.secret is not None
        assert validation_obj.success is not True
        validation_obj.mark_as_successful()
        assert validation_obj.secret is None
        assert validation_obj.success is True

    def test_mark_as_successful_raises_exceptions(self, validation_obj):
        """
        GIVEN: A Validation object which has succeeded or expired
        WHEN:  .mark_as_successful() is called on that object
        THEN:  An Exception should be raised
        """
        validation_obj.success = True

        with pytest.raises(Exception):
            validation_obj.mark_as_successful()

        validation_obj.success = False
        validation_obj.started_at = datetime.utcnow() - timedelta(days=10)

        with pytest.raises(Exception):
            validation_obj.mark_as_successful()

    def test_deadline_property(self, validation_obj):
        """
        GIVEN: A Validation object whose 'success' attribute does not evaluate to True
        WHEN:  That object's .deadline property is accessed
        THEN:  A datetime should be returned that is one day past the Validation's started_at time
        """
        started_at_value = datetime.utcnow()
        expected = started_at_value + Validation.EXPIRES_AFTER
        validation_obj.started_at = started_at_value

        assert validation_obj.deadline == expected

    def test_deadline_property_success_true(self, validation_obj):
        """
        GIVEN: A Validation object whose 'success' attribute does evaluate to True
        WHEN:  That object's .deadline property is accessed
        THEN:  None should be returned
        """
        validation_obj.success = True
        assert validation_obj.deadline is None

    def test_active_property(self, validation_obj):
        """
        GIVEN: A Validation object which is not yet successful and also not expired
        WHEN:  That object's .active property is accessed
        THEN:  True should be returned
        """
        assert validation_obj.active is True         # Success is false, expiry still in future

        validation_obj.success = True
        assert validation_obj.active is False        # Success is now true, so not active

        validation_obj.success = False
        validation_obj.started_at = datetime.utcnow() - timedelta(days=10)
        assert validation_obj.active is False        # Success is false, but expiry has passed
