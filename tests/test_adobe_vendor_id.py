import json

import pytest

import library_registry.drm.templates.adobe_xml_templates as t
from library_registry.drm.adobe_vendor_id import (
    AdobeSignInRequestParser,
    AdobeAccountInfoRequestParser,
    AdobeVendorIDClient,
    AdobeVendorIDRequestHandler,
    AdobeVendorIDModel,
    VendorIDAuthenticationError,
    VendorIDServerException,
)
from library_registry.config import Configuration
from library_registry.model import (
    DelegatedPatronIdentifier,
    ExternalIntegration,
)
from library_registry.util.short_client_token import ShortClientTokenEncoder
from library_registry.util.string_helpers import base64

TEST_NODE_VALUE = "0x685b35c00f05"
TEST_VENDOR_ID = "1045"


@pytest.fixture
def vendor_id_service(db_session, create_test_external_integration):
    integration = create_test_external_integration(
        db_session, protocol=ExternalIntegration.ADOBE_VENDOR_ID, goal=ExternalIntegration.DRM_GOAL
    )
    integration.setting(Configuration.ADOBE_VENDOR_ID).value = "VENDORID"
    integration.setting(Configuration.ADOBE_VENDOR_ID_NODE_VALUE).value = TEST_NODE_VALUE
    yield integration
    for setting_obj in integration.settings:
        db_session.delete(setting_obj)
    db_session.delete(integration)
    db_session.commit()


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
    @pytest.mark.skip(reason="TODO")
    @pytest.mark.needsdocstring
    def test_signin_handler(self):
        ...

    @pytest.mark.skip(reason="TODO")
    @pytest.mark.needsdocstring
    def test_userinfo_handler(self):
        ...

    @pytest.mark.skip(reason="TODO")
    @pytest.mark.needsdocstring
    def test_status_handler(self):
        ...


class TestConfiguration:
    @pytest.mark.needsdocstring
    def test_accessor(self, db_session, vendor_id_service):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        (vendor_id, node_value, delegates) = Configuration.vendor_id(db_session)
        assert vendor_id == "VENDORID"
        assert node_value == 114740953091845
        assert delegates == []

    @pytest.mark.needsdocstring
    def test_accessor_vendor_id_not_configured(self, db_session):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        (vendor_id, node_value, delegates) = Configuration.vendor_id(db_session)
        assert vendor_id is None
        assert node_value is None
        assert delegates == []

    @pytest.mark.needsdocstring
    def test_accessor_with_delegates(self, db_session, vendor_id_service):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        integration = vendor_id_service
        integration.setting(Configuration.ADOBE_VENDOR_ID_DELEGATE_URL).value = json.dumps(["delegate"])
        (vendor_id, node_value, delegates) = Configuration.vendor_id(db_session)
        assert vendor_id == "VENDORID"
        assert node_value == 114740953091845
        assert delegates == ["delegate"]


class TestVendorIDRequestParsers:
    @pytest.mark.needsdocstring
    def test_username_sign_in_request(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        merge_data = {"username": "Vendor username", "password": "Vendor password"}
        username_sign_in_request = t.SIGN_IN_REQUEST_TEMPLATE % merge_data
        parser = AdobeSignInRequestParser()
        data = parser.process(username_sign_in_request)
        assert data == {
            'username': merge_data["username"],
            'password': merge_data["password"],
            'method': 'standard',
        }

    @pytest.mark.needsdocstring
    def test_authdata_sign_in_request(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        merge_data = {"authdata": "dGhpcyBkYXRhIHdhcyBiYXNlNjQgZW5jb2RlZA=="}
        authdata_sign_in_request = t.AUTHDATA_SIGN_IN_REQUEST_TEMPLATE % merge_data
        parser = AdobeSignInRequestParser()
        data = parser.process(authdata_sign_in_request)
        assert data == {'authData': 'this data was base64 encoded', 'method': 'authData'}

    @pytest.mark.needsdocstring
    def test_accountinfo_request(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        merge_data = {"uuid": "urn:uuid:0xxxxxxx-xxxx-1xxx-xxxx-yyyyyyyyyyyy"}
        accountinfo_request = t.ACCOUNT_INFO_REQUEST_TEMPLATE % merge_data
        parser = AdobeAccountInfoRequestParser()
        data = parser.process(accountinfo_request)
        assert data == {'method': 'standard', 'user': merge_data["uuid"]}


class TestVendorIDRequestHandler:
    user1_uuid = "test-uuid"
    user1_label = "Human-readable label for user1"
    user1_signin_xml_response_body = t.SIGN_IN_RESPONSE_TEMPLATE % {"user": user1_uuid, "label": user1_label}
    username_password_lookup = {("user1", "pass1"): (user1_uuid, user1_label)}
    authdata_lookup = {"The secret token": (user1_uuid, user1_label)}
    userinfo_lookup = {user1_uuid: user1_label}

    @property
    def _handler(self):
        return AdobeVendorIDRequestHandler(TEST_VENDOR_ID)

    @classmethod
    def _standard_login(cls, data):
        return cls.username_password_lookup.get(
            (data.get('username'), data.get('password')), (None, None))

    @classmethod
    def _authdata_login(cls, authdata):
        return cls.authdata_lookup.get(authdata, (None, None))

    @classmethod
    def _userinfo(cls, uuid):
        return cls.userinfo_lookup.get(uuid)

    @pytest.mark.needsdocstring
    def test_error_document(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        doc = self._handler.error_document("VENDORID", "Some random error")
        assert doc == '<error xmlns="http://ns.adobe.com/adept" data="E_1045_VENDORID Some random error"/>'

    @pytest.mark.needsdocstring
    def test_handle_username_sign_in_request_success(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        merge_data = {"username": "user1", "password": "pass1"}
        signin_request_xml_body = t.SIGN_IN_REQUEST_TEMPLATE % merge_data
        result = self._handler.handle_signin_request(
            signin_request_xml_body,
            self._standard_login,
            self._authdata_login
        )
        assert result.startswith(self.user1_signin_xml_response_body)

    @pytest.mark.needsdocstring
    def test_handle_username_sign_in_request_failure(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        merge_data = {"username": self.user1_uuid, "password": "wrongpass"}
        signin_request_xml_body = t.SIGN_IN_REQUEST_TEMPLATE % merge_data
        result = self._handler.handle_signin_request(
            signin_request_xml_body,
            self._standard_login,
            self._authdata_login
        )
        expected = t.ERROR_RESPONSE_TEMPLATE % {
            "vendor_id": TEST_VENDOR_ID,
            "type": "AUTH",
            "message": "Incorrect barcode or PIN.",
        }
        assert result == expected

    @pytest.mark.needsdocstring
    def test_handle_username_authdata_request_success(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        secret_token = base64.b64encode("The secret token")
        authdata_request_xml_body = t.AUTHDATA_SIGN_IN_REQUEST_TEMPLATE % {"authdata": secret_token}
        result = self._handler.handle_signin_request(
            authdata_request_xml_body,
            self._standard_login,
            self._authdata_login
        )
        assert result.startswith(self.user1_signin_xml_response_body)

    @pytest.mark.needsdocstring
    def test_handle_username_authdata_request_invalid(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        doc = t.AUTHDATA_SIGN_IN_REQUEST_TEMPLATE % dict(authdata="incorrect")
        result = self._handler.handle_signin_request(doc, self._standard_login, self._authdata_login)
        assert result.startswith(f'<error xmlns="http://ns.adobe.com/adept" data="E_{TEST_VENDOR_ID}_AUTH')

    @pytest.mark.needsdocstring
    def test_handle_username_authdata_request_failure(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        doc = t.AUTHDATA_SIGN_IN_REQUEST_TEMPLATE % dict(authdata=base64.b64encode("incorrect"))
        result = self._handler.handle_signin_request(doc, self._standard_login, self._authdata_login)
        expected = t.ERROR_RESPONSE_TEMPLATE % {
            "vendor_id": TEST_VENDOR_ID,
            "type": "AUTH",
            "message": "Incorrect token.",
        }
        assert result == expected

    @pytest.mark.needsdocstring
    def test_failure_send_login_request_to_accountinfo(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        doc = t.AUTHDATA_SIGN_IN_REQUEST_TEMPLATE % dict(authdata=base64.b64encode("incorrect"))
        result = self._handler.handle_accountinfo_request(doc, self._userinfo)
        expected = t.ERROR_RESPONSE_TEMPLATE % {
            "vendor_id": TEST_VENDOR_ID,
            "type": "ACCOUNT_INFO",
            "message": "Request document in wrong format.",
        }
        assert result == expected

    @pytest.mark.needsdocstring
    def test_failure_send_accountinfo_request_to_login(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        doc = t.ACCOUNT_INFO_REQUEST_TEMPLATE % dict(uuid=self.user1_uuid)
        result = self._handler.handle_signin_request(doc, self._standard_login, self._authdata_login)
        expected = t.ERROR_RESPONSE_TEMPLATE % {
            "vendor_id": TEST_VENDOR_ID,
            "type": "AUTH",
            "message": "Request document in wrong format.",
        }
        assert result == expected

    @pytest.mark.needsdocstring
    def test_handle_accountinfo_success(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        doc = t.ACCOUNT_INFO_REQUEST_TEMPLATE % dict(uuid=self.user1_uuid)
        result = self._handler.handle_accountinfo_request(doc, self._userinfo)
        expected = t.ACCOUNT_INFO_RESPONSE_TEMPLATE % {"label": self.user1_label}
        assert result == expected

    @pytest.mark.needsdocstring
    def test_handle_accountinfo_failure(self):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        merge_data = {"uuid": "not the uuid"}
        doc = t.ACCOUNT_INFO_REQUEST_TEMPLATE % merge_data
        result = self._handler.handle_accountinfo_request(doc, self._userinfo)
        expected = t.ERROR_RESPONSE_TEMPLATE % {
            "vendor_id": TEST_VENDOR_ID,
            "type": "ACCOUNT_INFO",
            "message": f"Could not identify patron from '{merge_data['uuid']}'.",
        }
        assert result == expected


@pytest.fixture
def vendor_id_model_library(db_session, create_test_library):
    library_obj = create_test_library(db_session)
    yield library_obj


@pytest.fixture
def vendor_id_model_integration(db_session, create_test_external_integration):
    integration_obj = create_test_external_integration(db_session)
    yield integration_obj
    db_session.delete(integration_obj)
    db_session.commit()


@pytest.fixture
def vendor_id_model(db_session, vendor_id_model_library, vendor_id_model_integration):
    (_, node_value, delegates) = Configuration.vendor_id(db_session)
    model_obj = AdobeVendorIDModel(db_session, node_value, delegates)
    yield model_obj
    del model_obj


class TestVendorIDModel:
    @pytest.mark.needsdocstring
    def test_short_client_token_lookup_success(self, db_session, vendor_id_model, vendor_id_model_library):
        """
        GIVEN:
        WHEN:
        THEN:
        """
        # Test that the library registry can perform an authdata lookup or a standard lookup on a
        # short client token generated by one of its libraries.

        # Over on a library's circulation manager, a short client token is created for one of the patrons.
        encoder = ShortClientTokenEncoder()
        patron_alias = "patron alias"
        short_client_token = encoder.encode(
            vendor_id_model_library.short_name,
            vendor_id_model_library.shared_secret,
            patron_alias
        )

        # Here at the library registry, we can validate the short client token as authdata and create a
        # DelegatedPatronIdentifier for that patron.
        (account_id, label) = vendor_id_model.authdata_lookup(short_client_token)
        assert account_id.startswith('urn:uuid:0')
        assert label == f"Delegated account ID {account_id}"

        # The UUID corresponds to a DelegatedPatronIdentifier, associated with the foreign library and the
        # patron identifier that library encoded in its JWT.
        [dpi] = db_session.query(DelegatedPatronIdentifier).all()
        assert dpi.patron_identifier == patron_alias
        assert dpi.delegated_identifier == account_id
        assert dpi.library == vendor_id_model_library

        # The label is the same one we get by calling urn_to_label.
        assert vendor_id_model.urn_to_label(account_id) == label

        # We get the same UUID and label by splitting the short client token into a 'token' part and a
        # 'signature' part, and passing the token and signature to standard_lookup as username and password.
        token, signature = short_client_token.rsplit('|', 1)
        credentials = {"username": token, "password": signature}
        (new_account_id, new_label) = vendor_id_model.standard_lookup(credentials)
        assert new_account_id == account_id
        assert new_label == label

    @pytest.mark.needsdocstring
    def test_short_client_token_lookup_failure(self, vendor_id_model, vendor_id_model_library):
        """
        An invalid short client token will not be turned into an Adobe Account ID.

        GIVEN:
        WHEN:
        THEN:
        """
        assert vendor_id_model.standard_lookup(dict(username="bad token", password="bad signature")) == (None, None)
        assert vendor_id_model.authdata_lookup(None) == (None, None)
        assert vendor_id_model.authdata_lookup('badauthdata') == (None, None)

        # This token is correctly formed but the signature doesn't match.
        encoder = ShortClientTokenEncoder()
        bad_signature = encoder.encode(
            vendor_id_model_library.short_name,
            vendor_id_model_library.shared_secret + "bad",
            "patron alias"
        )
        assert vendor_id_model.authdata_lookup(bad_signature) == (None, None)

    @pytest.mark.needsdocstring
    def test_delegation_standard_lookup(self, db_session, vendor_id_model_library):
        """
        A model that doesn't know how to handle a login request can delegate to another Vendor ID server.

        GIVEN:
        WHEN:
        THEN:
        """
        delegate1 = MockAdobeVendorIDClient()
        delegate2 = MockAdobeVendorIDClient()

        # Delegate 1 can't verify this user.
        delegate1.enqueue(VendorIDAuthenticationError("Nope"))

        # Delegate 2 can.
        delegate2.enqueue(("adobe_id", "label", "content"))

        delegates = [delegate1, delegate2]
        model = AdobeVendorIDModel(db_session, TEST_NODE_VALUE, delegates)

        # This isn't a valid Short Client Token, but as long as
        # a delegate can decode it, that doesn't matter.
        username = vendor_id_model_library.short_name + "|1234|someuser"

        result = model.standard_lookup({"username": username, "password": "password"})
        assert result == ("adobe_id", "Delegated account ID adobe_id")

        # We tried delegate 1 before getting the answer from delegate 2.
        assert delegate1.queue == []
        assert delegate2.queue == []

        # A DelegatedPatronIdentifier was created to store the information we got from the delegate.
        [delegated] = vendor_id_model_library.delegated_patron_identifiers
        assert delegated.patron_identifier == "someuser"
        assert delegated.delegated_identifier == "adobe_id"
        assert delegated.type == DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID

        # Now test with a username/password that's not a Short Client Token at all.
        delegate1.enqueue(("adobe_id_2", "label_2", "content"))
        result = model.standard_lookup({"username": "not a short client token", "password": "some password"})

        # delegate1 provided the answer, and we used it as is.
        assert result == ("adobe_id_2", "label_2")

        # We did not create a local DelegatedPatronIdentifier, because we don't know which Library the patron
        # should be associated with.
        assert vendor_id_model_library.delegated_patron_identifiers == [delegated]

    @pytest.mark.needsdocstring
    def test_delegation_authdata_lookup(self, db_session, vendor_id_model_library):
        """
        Test the ability to delegate an authdata login request to another server.

        GIVEN:
        WHEN:
        THEN:
        """
        delegate1 = MockAdobeVendorIDClient()
        delegate2 = MockAdobeVendorIDClient()
        delegates = [delegate1, delegate2]
        model = AdobeVendorIDModel(db_session, TEST_NODE_VALUE, delegates)

        # First, test an authdata that is a Short Client Token.

        # Delegate 1 can verify the authdata
        delegate1.enqueue(("adobe_id", "label", "content"))

        # Delegate 2 is broken.
        delegate2.enqueue(VendorIDServerException("blah"))

        authdata = vendor_id_model_library.short_name + "|1234|authdatauser|password"
        result = model.authdata_lookup(authdata)
        assert result == ("adobe_id", "Delegated account ID adobe_id")

        # We didn't even get to delegate 2.
        assert len(delegate2.queue) == 1

        [delegated] = vendor_id_model_library.delegated_patron_identifiers
        assert delegated.patron_identifier == "authdatauser"
        assert delegated.delegated_identifier == "adobe_id"
        assert delegated.type == DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID

        # If we try it again, we'll get an error from delegate 1, since nothing is queued up, and then
        # a queued error from delegate 2. Then we'll try to decode the token ourselves, but since it's
        # not a valid Short Client Token, we'll get an error there, and return nothing.
        result = model.authdata_lookup(authdata)
        assert result == (None, None)
        assert delegate2.queue == []

        # Finally, test authentication by treating some random data as authdata.

        # Delegate 1 can verify the authdata
        delegate1.enqueue(("adobe_id_3", "label", "content"))

        # Delegate 2 is broken.
        delegate2.enqueue(VendorIDServerException("blah"))

        # This authdata is not a Short Client Token. We will ask the delegates to authenticate it, and
        # when one succeeds we will pass on the answer exactly as is. We can't create a
        # DelegatedPatronIdentifier, because we don't know which library originated the authdata or what
        # the library's patron identifier is.
        result = model.authdata_lookup("Some random authdata")
        assert result == ("adobe_id_3", "label")

        assert vendor_id_model_library.delegated_patron_identifiers == [delegated]

        # We didn't get to delegate 2, because delegate 1 had the answer.
        assert len(delegate2.queue) == 1
