import json

import pytest

import adobe_xml_templates as t
from adobe_vendor_id import (
    AdobeAccountInfoRequestParser,
    AdobeSignInRequestParser,
    AdobeVendorIDClient,
    AdobeVendorIDModel,
    AdobeVendorIDRequestHandler,
    VendorIDAuthenticationError,
    VendorIDServerException,
)
from config import Configuration
from model import DelegatedPatronIdentifier, ExternalIntegration, create
from tests.fixtures.database import DatabaseTransactionFixture
from util.short_client_token import ShortClientTokenEncoder
from util.string_helpers import base64


class MockAdobeVendorIDClient(AdobeVendorIDClient):
    """Mock AdobeVendorIDClient for use in tests."""

    def __init__(self):
        self.queue = []

    def enqueue(self, response):
        """Queue a response."""
        self.queue.insert(0, response)

    def dequeue(self, *args, **kwargs):
        """Dequeue a response. If it's an exception, raise it. Otherwise return it."""
        if not self.queue:
            raise VendorIDServerException("No response queued.")

        response = self.queue.pop()

        if isinstance(response, Exception):
            raise response

        return response

    status = dequeue
    sign_in_authdata = dequeue
    sign_in_standard = dequeue
    user_info = dequeue


class TestAdobeVendorIdController:
    def test_signin_handler(self): ...

    def test_userinfo_handler(self): ...

    def test_status_handler(self): ...


class VendorIDFixture:
    NODE_VALUE = "0x685b35c00f05"

    def __init__(self, db: DatabaseTransactionFixture):
        self.db = db

    def integration(self):
        """Configure a basic Vendor ID Service setup."""

        integration, ignore = create(
            self.db.session,
            ExternalIntegration,
            protocol=ExternalIntegration.ADOBE_VENDOR_ID,
            goal=ExternalIntegration.DRM_GOAL,
        )
        integration.setting(Configuration.ADOBE_VENDOR_ID).value = "VENDORID"
        integration.setting(Configuration.ADOBE_VENDOR_ID_NODE_VALUE).value = (
            self.NODE_VALUE
        )
        return integration


@pytest.fixture(scope="function")
def vendor_id_fixture(db: DatabaseTransactionFixture) -> VendorIDFixture:
    return VendorIDFixture(db)


class TestConfiguration:
    def test_accessor(self, vendor_id_fixture: VendorIDFixture):
        vendor_id_fixture.integration()
        vendor_id, node_value, delegates = Configuration.vendor_id(
            vendor_id_fixture.db.session
        )
        assert vendor_id == "VENDORID"
        assert node_value == 114740953091845
        assert delegates == []

    def test_accessor_vendor_id_not_configured(
        self, vendor_id_fixture: VendorIDFixture
    ):
        vendor_id, node_value, delegates = Configuration.vendor_id(
            vendor_id_fixture.db.session
        )
        assert vendor_id is None
        assert node_value is None
        assert delegates == []

    def test_accessor_with_delegates(self, vendor_id_fixture: VendorIDFixture):
        integration = vendor_id_fixture.integration()
        integration.setting(Configuration.ADOBE_VENDOR_ID_DELEGATE_URL).value = (
            json.dumps(["delegate"])
        )
        vendor_id, node_value, delegates = Configuration.vendor_id(
            vendor_id_fixture.db.session
        )
        assert vendor_id == "VENDORID"
        assert node_value == 114740953091845
        assert delegates == ["delegate"]


class TestVendorIDRequestParsers:
    username_sign_in_request = t.SIGN_IN_REQUEST_TEMPLATE % {
        "username": "Vendor username",
        "password": "Vendor password",
    }
    authdata_sign_in_request = t.AUTHDATA_SIGN_IN_REQUEST_TEMPLATE % {
        "authdata": "dGhpcyBkYXRhIHdhcyBiYXNlNjQgZW5jb2RlZA=="
    }
    accountinfo_request = t.ACCOUNT_INFO_REQUEST_TEMPLATE % {
        "uuid": "urn:uuid:0xxxxxxx-xxxx-1xxx-xxxx-yyyyyyyyyyyy"
    }

    def test_username_sign_in_request(self):
        parser = AdobeSignInRequestParser()
        data = parser.process(self.username_sign_in_request)
        assert data == {
            "username": "Vendor username",
            "password": "Vendor password",
            "method": "standard",
        }

    def test_authdata_sign_in_request(self):
        parser = AdobeSignInRequestParser()
        data = parser.process(self.authdata_sign_in_request)
        assert data == {
            "authData": "this data was base64 encoded",
            "method": "authData",
        }

    def test_accountinfo_request(self):
        parser = AdobeAccountInfoRequestParser()
        data = parser.process(self.accountinfo_request)
        assert data == {
            "method": "standard",
            "user": "urn:uuid:0xxxxxxx-xxxx-1xxx-xxxx-yyyyyyyyyyyy",
        }


class TestVendorIDRequestHandler:
    username_sign_in_request = t.SIGN_IN_REQUEST_TEMPLATE
    accountinfo_request = t.ACCOUNT_INFO_REQUEST_TEMPLATE

    TEST_VENDOR_ID = "1045"

    user1_uuid = "test-uuid"
    user1_label = "Human-readable label for user1"
    user1_signin_xml_response_body = t.SIGN_IN_RESPONSE_TEMPLATE % {
        "user": user1_uuid,
        "label": user1_label,
    }
    username_password_lookup = {("user1", "pass1"): (user1_uuid, user1_label)}
    authdata_lookup = {"The secret token": (user1_uuid, user1_label)}
    userinfo_lookup = {user1_uuid: user1_label}

    @property
    def _handler(self):
        return AdobeVendorIDRequestHandler(self.TEST_VENDOR_ID)

    @classmethod
    def _standard_login(cls, data):
        return cls.username_password_lookup.get(
            (data.get("username"), data.get("password")), (None, None)
        )

    @classmethod
    def _authdata_login(cls, authdata):
        return cls.authdata_lookup.get(authdata, (None, None))

    @classmethod
    def _userinfo(cls, uuid):
        return cls.userinfo_lookup.get(uuid)

    def test_error_document(self):
        doc = self._handler.error_document("VENDORID", "Some random error")
        assert (
            doc
            == '<error xmlns="http://ns.adobe.com/adept" data="E_1045_VENDORID Some random error"/>'
        )

    def test_handle_username_sign_in_request_success(self):
        signin_request_xml_body = t.SIGN_IN_REQUEST_TEMPLATE % {
            "username": "user1",
            "password": "pass1",
        }
        result = self._handler.handle_signin_request(
            signin_request_xml_body, self._standard_login, self._authdata_login
        )
        assert result.startswith(self.user1_signin_xml_response_body)

    def test_handle_username_sign_in_request_failure(self):
        signin_request_xml_body = t.SIGN_IN_REQUEST_TEMPLATE % {
            "username": self.user1_uuid,
            "password": "wrongpass",
        }
        result = self._handler.handle_signin_request(
            signin_request_xml_body, self._standard_login, self._authdata_login
        )
        expected = t.ERROR_RESPONSE_TEMPLATE % {
            "vendor_id": "1045",
            "type": "AUTH",
            "message": "Incorrect barcode or PIN.",
        }
        assert result == expected

    def test_handle_username_authdata_request_success(self):
        secret_token = base64.b64encode("The secret token")
        authdata_request_xml_body = t.AUTHDATA_SIGN_IN_REQUEST_TEMPLATE % {
            "authdata": secret_token
        }
        result = self._handler.handle_signin_request(
            authdata_request_xml_body, self._standard_login, self._authdata_login
        )
        assert result.startswith(self.user1_signin_xml_response_body)

    def test_handle_username_authdata_request_invalid(self):
        doc = t.AUTHDATA_SIGN_IN_REQUEST_TEMPLATE % dict(authdata="incorrect")
        result = self._handler.handle_signin_request(
            doc, self._standard_login, self._authdata_login
        )
        assert result.startswith(
            '<error xmlns="http://ns.adobe.com/adept" data="E_1045_AUTH'
        )

    def test_handle_username_authdata_request_failure(self):
        doc = t.AUTHDATA_SIGN_IN_REQUEST_TEMPLATE % dict(
            authdata=base64.b64encode("incorrect")
        )
        result = self._handler.handle_signin_request(
            doc, self._standard_login, self._authdata_login
        )
        expected = t.ERROR_RESPONSE_TEMPLATE % {
            "vendor_id": "1045",
            "type": "AUTH",
            "message": "Incorrect token.",
        }
        assert result == expected

    def test_failure_send_login_request_to_accountinfo(self):
        doc = t.AUTHDATA_SIGN_IN_REQUEST_TEMPLATE % dict(
            authdata=base64.b64encode("incorrect")
        )
        result = self._handler.handle_accountinfo_request(doc, self._userinfo)
        expected = t.ERROR_RESPONSE_TEMPLATE % {
            "vendor_id": "1045",
            "type": "ACCOUNT_INFO",
            "message": "Request document in wrong format.",
        }
        assert result == expected

    def test_failure_send_accountinfo_request_to_login(self):
        doc = self.accountinfo_request % dict(uuid=self.user1_uuid)
        result = self._handler.handle_signin_request(
            doc, self._standard_login, self._authdata_login
        )
        expected = t.ERROR_RESPONSE_TEMPLATE % {
            "vendor_id": "1045",
            "type": "AUTH",
            "message": "Request document in wrong format.",
        }
        assert result == expected

    def test_handle_accountinfo_success(self):
        doc = self.accountinfo_request % dict(uuid=self.user1_uuid)
        result = self._handler.handle_accountinfo_request(doc, self._userinfo)
        expected = t.ACCOUNT_INFO_RESPONSE_TEMPLATE % {"label": self.user1_label}
        assert result == expected

    def test_handle_accountinfo_failure(self):
        doc = self.accountinfo_request % dict(uuid="not the uuid")
        result = self._handler.handle_accountinfo_request(doc, self._userinfo)
        expected = t.ERROR_RESPONSE_TEMPLATE % {
            "vendor_id": "1045",
            "type": "ACCOUNT_INFO",
            "message": "Could not identify patron from 'not the uuid'.",
        }
        assert result == expected


class VendorIDModelFixture:
    def __init__(self, vendor_id_fixture: VendorIDFixture):
        self.vendor_id_fixture = vendor_id_fixture
        vendor_id_fixture.integration()
        vendor_id, node_value, delegates = Configuration.vendor_id(
            self.vendor_id_fixture.db.session
        )
        self.model = AdobeVendorIDModel(
            self.vendor_id_fixture.db.session, node_value, delegates
        )
        # Here's a library that participates in the registry.
        self.library = vendor_id_fixture.db.library()


@pytest.fixture(scope="function")
def vendor_id_model_fixture(vendor_id_fixture: VendorIDFixture) -> VendorIDModelFixture:
    return VendorIDModelFixture(vendor_id_fixture)


class TestVendorIDModel:
    def test_short_client_token_lookup_success(
        self, vendor_id_model_fixture: VendorIDModelFixture
    ):
        library, model, db = (
            vendor_id_model_fixture.library,
            vendor_id_model_fixture.model,
            vendor_id_model_fixture.vendor_id_fixture.db,
        )

        # Test that the library registry can perform an authdata lookup or a
        # standard lookup on a short client token generated by one of
        # its libraries.

        # Over on a library's circulation manager, a short client token
        # is created for one of the patrons.
        encoder = ShortClientTokenEncoder()
        short_client_token = encoder.encode(
            library.short_name, library.shared_secret, "patron alias"
        )

        # Here at the library registry, we can validate the short
        # client token as authdata and create a
        # DelegatedPatronIdentifier for that patron.
        account_id, label = model.authdata_lookup(short_client_token)
        assert account_id.startswith("urn:uuid:0")
        assert label == "Delegated account ID %s" % account_id

        # The UUID corresponds to a DelegatedPatronIdentifier,
        # associated with the foreign library and the patron
        # identifier that library encoded in its JWT.
        [dpi] = db.session.query(DelegatedPatronIdentifier).all()
        assert dpi.patron_identifier == "patron alias"
        assert dpi.delegated_identifier == account_id
        assert dpi.library == library

        # The label is the same one we get by calling urn_to_label.
        assert model.urn_to_label(account_id) == label

        # We get the same UUID and label by splitting the short client
        # token into a 'token' part and a 'signature' part, and
        # passing the token and signature to standard_lookup as
        # username and password.
        token, signature = short_client_token.rsplit("|", 1)
        credentials = dict(username=token, password=signature)
        new_account_id, new_label = model.standard_lookup(credentials)
        assert new_account_id == account_id
        assert new_label == label

    def test_short_client_token_lookup_failure(
        self, vendor_id_model_fixture: VendorIDModelFixture
    ):
        library, model = vendor_id_model_fixture.library, vendor_id_model_fixture.model

        """An invalid short client token will not be turned into an
        Adobe Account ID.
        """
        assert model.standard_lookup(
            dict(username="bad token", password="bad signature")
        ) == (None, None)

        assert model.authdata_lookup(None) == (None, None)
        assert model.authdata_lookup("badauthdata") == (None, None)

        # This token is correctly formed but the signature doesn't match.
        encoder = ShortClientTokenEncoder()
        bad_signature = encoder.encode(
            library.short_name, library.shared_secret + "bad", "patron alias"
        )
        assert model.authdata_lookup(bad_signature) == (None, None)

    def test_delegation_standard_lookup(
        self, vendor_id_model_fixture: VendorIDModelFixture
    ):
        library, model, db = (
            vendor_id_model_fixture.library,
            vendor_id_model_fixture.model,
            vendor_id_model_fixture.vendor_id_fixture.db,
        )

        """A model that doesn't know how to handle a login request can delegate to another Vendor ID server."""
        delegate1 = MockAdobeVendorIDClient()
        delegate2 = MockAdobeVendorIDClient()

        # Delegate 1 can't verify this user.
        delegate1.enqueue(VendorIDAuthenticationError("Nope"))

        # Delegate 2 can.
        delegate2.enqueue(("adobe_id", "label", "content"))

        delegates = [delegate1, delegate2]
        model = AdobeVendorIDModel(
            db.session, vendor_id_model_fixture.vendor_id_fixture.NODE_VALUE, delegates
        )

        # This isn't a valid Short Client Token, but as long as
        # a delegate can decode it, that doesn't matter.
        username = library.short_name + "|1234|someuser"

        result = model.standard_lookup(dict(username=username, password="password"))
        assert result == ("adobe_id", "Delegated account ID adobe_id")

        # We tried delegate 1 before getting the answer from delegate 2.
        assert delegate1.queue == []
        assert delegate2.queue == []

        # A DelegatedPatronIdentifier was created to store the information
        # we got from the delegate.
        [delegated] = library.delegated_patron_identifiers
        assert delegated.patron_identifier == "someuser"
        assert delegated.delegated_identifier == "adobe_id"
        assert delegated.type == DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID

        # Now test with a username/password that's not a Short Client Token
        # at all.
        delegate1.enqueue(("adobe_id_2", "label_2", "content"))
        result = model.standard_lookup(
            dict(username="not a short client token", password="some password")
        )

        # delegate1 provided the answer, and we used it as is.
        assert result == ("adobe_id_2", "label_2")

        # We did not create a local DelegatedPatronIdentifier, because
        # we don't know which Library the patron should be associated
        # with.
        assert library.delegated_patron_identifiers == [delegated]

    def test_delegation_authdata_lookup(
        self, vendor_id_model_fixture: VendorIDModelFixture
    ):
        library, model, db = (
            vendor_id_model_fixture.library,
            vendor_id_model_fixture.model,
            vendor_id_model_fixture.vendor_id_fixture.db,
        )

        """Test the ability to delegate an authdata login request
        to another server.
        """
        delegate1 = MockAdobeVendorIDClient()
        delegate2 = MockAdobeVendorIDClient()
        delegates = [delegate1, delegate2]
        model = AdobeVendorIDModel(
            db.session, vendor_id_model_fixture.vendor_id_fixture.NODE_VALUE, delegates
        )

        # First, test an authdata that is a Short Client Token.

        # Delegate 1 can verify the authdata
        delegate1.enqueue(("adobe_id", "label", "content"))

        # Delegate 2 is broken.
        delegate2.enqueue(VendorIDServerException("blah"))

        authdata = library.short_name + "|1234|authdatauser|password"
        result = model.authdata_lookup(authdata)
        assert result == ("adobe_id", "Delegated account ID adobe_id")

        # We didn't even get to delegate 2.
        assert len(delegate2.queue) == 1

        [delegated] = library.delegated_patron_identifiers
        assert delegated.patron_identifier == "authdatauser"
        assert delegated.delegated_identifier == "adobe_id"
        assert delegated.type == DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID

        # If we try it again, we'll get an error from delegate 1,
        # since nothing is queued up, and then a queued error from
        # delegate 2. Then we'll try to decode the token
        # ourselves, but since it's not a valid Short Client Token,
        # we'll get an error there, and return nothing.
        result = model.authdata_lookup(authdata)
        assert result == (None, None)
        assert delegate2.queue == []

        # Finally, test authentication by treating some random data
        # as authdata.

        # Delegate 1 can verify the authdata
        delegate1.enqueue(("adobe_id_3", "label", "content"))

        # Delegate 2 is broken.
        delegate2.enqueue(VendorIDServerException("blah"))

        # This authdata is not a Short Client Token. We will ask the
        # delegates to authenticate it, and when one succeeds we will
        # pass on the answer exactly as is. We can't create a
        # DelegatedPatronIdentifier, because we don't know which
        # library originated the authdata or what the library's patron
        # identifier is.
        result = model.authdata_lookup("Some random authdata")
        assert result == ("adobe_id_3", "label")

        assert library.delegated_patron_identifiers == [delegated]

        # We didn't get to delegate 2, because delegate 1 had the answer.
        assert len(delegate2.queue) == 1
