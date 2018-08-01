import base64
import datetime
from nose.tools import (
    assert_raises_regexp,
    eq_,
    set_trace,
)
import json
from config import (
    CannotLoadConfiguration,
    Configuration,
    temp_config,
)

from adobe_vendor_id import (
    AdobeSignInRequestParser,
    AdobeAccountInfoRequestParser,
    AdobeVendorIDRequestHandler,
    AdobeVendorIDModel,
    MockAdobeVendorIDClient,
    VendorIDAuthenticationError,
    VendorIDServerException,
)

from model import(
    DelegatedPatronIdentifier,
    ExternalIntegration,
    create,
)

from util.short_client_token import ShortClientTokenEncoder

from . import (
    DatabaseTest,
)

class VendorIDTest(DatabaseTest):

    NODE_VALUE = "0x685b35c00f05"

    def _integration(self):
        """Configure a basic Vendor ID Service setup."""

        integration, ignore = create(
            self._db, ExternalIntegration,
            protocol=ExternalIntegration.ADOBE_VENDOR_ID,
            goal=ExternalIntegration.DRM_GOAL,
        )
        integration.setting(Configuration.ADOBE_VENDOR_ID).value = "VENDORID"
        integration.setting(Configuration.ADOBE_VENDOR_ID_NODE_VALUE).value = self.NODE_VALUE
        return integration

class TestConfiguration(VendorIDTest):

    def test_accessor(self):
        self._integration()
        vendor_id, node_value, delegates = Configuration.vendor_id(self._db)
        eq_("VENDORID", vendor_id)
        eq_(114740953091845, node_value)
        eq_([], delegates)

    def test_accessor_vendor_id_not_configured(self):
        vendor_id, node_value, delegates = Configuration.vendor_id(self._db)
        eq_(None, vendor_id)
        eq_(None, node_value)
        eq_([], delegates)

    def test_accessor_with_delegates(self):
        integration = self._integration()
        integration.setting(Configuration.ADOBE_VENDOR_ID_DELEGATE_URL).value = json.dumps(["delegate"])
        vendor_id, node_value, delegates = Configuration.vendor_id(self._db)
        eq_("VENDORID", vendor_id)
        eq_(114740953091845, node_value)
        eq_(["delegate"], delegates)


class TestVendorIDRequestParsers(object):

    username_sign_in_request = """<signInRequest method="standard" xmlns="http://ns.adobe.com/adept">
<username>Vendor username</username>
<password>Vendor password</password>
</signInRequest>"""

    authdata_sign_in_request = """<signInRequest method="authData" xmlns="http://ns.adobe.com/adept">
<authData> dGhpcyBkYXRhIHdhcyBiYXNlNjQgZW5jb2RlZA== </authData>
</signInRequest>"""

    accountinfo_request = """<accountInfoRequest method="standard" xmlns="http://ns.adobe.com/adept">
<user>urn:uuid:0xxxxxxx-xxxx-1xxx-xxxx-yyyyyyyyyyyy</user>
</accountInfoRequest >"""

    def test_username_sign_in_request(self):
        parser = AdobeSignInRequestParser()
        data = parser.process(self.username_sign_in_request)
        eq_({'username': 'Vendor username',
             'password': 'Vendor password', 'method': 'standard'}, data)

    def test_authdata_sign_in_request(self):
        parser = AdobeSignInRequestParser()
        data = parser.process(self.authdata_sign_in_request)
        eq_({'authData': 'this data was base64 encoded', 'method': 'authData'},
            data)

    def test_accountinfo_request(self):
        parser = AdobeAccountInfoRequestParser()
        data = parser.process(self.accountinfo_request)
        eq_({'method': 'standard',
             'user': 'urn:uuid:0xxxxxxx-xxxx-1xxx-xxxx-yyyyyyyyyyyy'},
            data)

class TestVendorIDRequestHandler(object):

    username_sign_in_request = """<signInRequest method="standard" xmlns="http://ns.adobe.com/adept">
<username>%(username)s</username>
<password>%(password)s</password>
</signInRequest>"""

    authdata_sign_in_request = """<signInRequest method="authData" xmlns="http://ns.adobe.com/adept">
<authData>%(authdata)s</authData>
</signInRequest>"""

    accountinfo_request = """<accountInfoRequest method="standard" xmlns="http://ns.adobe.com/adept">
<user>%(uuid)s</user>
</accountInfoRequest >"""

    TEST_VENDOR_ID = "1045"

    user1_uuid = "test-uuid"
    user1_label = "Human-readable label for user1"
    username_password_lookup = {
        ("user1", "pass1") : (user1_uuid, user1_label)
    }

    authdata_lookup = {
        "The secret token" : (user1_uuid, user1_label)
    }

    userinfo_lookup = { user1_uuid : user1_label }

    @property
    def _handler(self):
        return AdobeVendorIDRequestHandler(
            self.TEST_VENDOR_ID)

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

    def test_error_document(self):
        doc = self._handler.error_document(
            "VENDORID", "Some random error")
        eq_('<error xmlns="http://ns.adobe.com/adept" data="E_1045_VENDORID Some random error"/>', doc)

    def test_handle_username_sign_in_request_success(self):
        doc = self.username_sign_in_request % dict(
            username="user1", password="pass1")
        result = self._handler.handle_signin_request(
            doc, self._standard_login, self._authdata_login)
        assert result.startswith('<signInResponse xmlns="http://ns.adobe.com/adept">\n<user>test-uuid</user>\n<label>Human-readable label for user1</label>\n</signInResponse>')

    def test_handle_username_sign_in_request_failure(self):
        doc = self.username_sign_in_request % dict(
            username="user1", password="wrongpass")
        result = self._handler.handle_signin_request(
            doc, self._standard_login, self._authdata_login)
        eq_('<error xmlns="http://ns.adobe.com/adept" data="E_1045_AUTH Incorrect barcode or PIN."/>', result)

    def test_handle_username_authdata_request_success(self):
        doc = self.authdata_sign_in_request % dict(
            authdata=base64.b64encode("The secret token"))
        result = self._handler.handle_signin_request(
            doc, self._standard_login, self._authdata_login)
        assert result.startswith('<signInResponse xmlns="http://ns.adobe.com/adept">\n<user>test-uuid</user>\n<label>Human-readable label for user1</label>\n</signInResponse>')

    def test_handle_username_authdata_request_invalid(self):
        doc = self.authdata_sign_in_request % dict(
            authdata="incorrect")
        result = self._handler.handle_signin_request(
            doc, self._standard_login, self._authdata_login)
        assert result.startswith('<error xmlns="http://ns.adobe.com/adept" data="E_1045_AUTH')

    def test_handle_username_authdata_request_failure(self):
        doc = self.authdata_sign_in_request % dict(
            authdata=base64.b64encode("incorrect"))
        result = self._handler.handle_signin_request(
            doc, self._standard_login, self._authdata_login)
        eq_('<error xmlns="http://ns.adobe.com/adept" data="E_1045_AUTH Incorrect token."/>', result)

    def test_failure_send_login_request_to_accountinfo(self):
        doc = self.authdata_sign_in_request % dict(
            authdata=base64.b64encode("incorrect"))
        result = self._handler.handle_accountinfo_request(
            doc, self._userinfo)
        eq_('<error xmlns="http://ns.adobe.com/adept" data="E_1045_ACCOUNT_INFO Request document in wrong format."/>', result)

    def test_failure_send_accountinfo_request_to_login(self):
        doc = self.accountinfo_request % dict(
            uuid=self.user1_uuid)
        result = self._handler.handle_signin_request(
            doc, self._standard_login, self._authdata_login)
        eq_('<error xmlns="http://ns.adobe.com/adept" data="E_1045_AUTH Request document in wrong format."/>', result)

    def test_handle_accountinfo_success(self):
        doc = self.accountinfo_request % dict(
            uuid=self.user1_uuid)
        result = self._handler.handle_accountinfo_request(
            doc, self._userinfo)
        eq_('<accountInfoResponse xmlns="http://ns.adobe.com/adept">\n<label>Human-readable label for user1</label>\n</accountInfoResponse>', result)

    def test_handle_accountinfo_failure(self):
        doc = self.accountinfo_request % dict(
            uuid="not the uuid")
        result = self._handler.handle_accountinfo_request(
            doc, self._userinfo)
        eq_('<error xmlns="http://ns.adobe.com/adept" data="E_1045_ACCOUNT_INFO Could not identify patron from \'not the uuid\'."/>', result)


class TestVendorIDModel(VendorIDTest):

    def setup(self):
        super(TestVendorIDModel, self).setup()
        self._integration()
        vendor_id, node_value, delegates = Configuration.vendor_id(self._db)
        self.model = AdobeVendorIDModel(self._db, node_value, delegates)

        # Here's a library that participates in the registry.
        self.library = self._library()

    def test_short_client_token_lookup_success(self):
        """Test that the library registry can perform an authdata lookup or a
        standard lookup on a short client token generated by one of
        its libraries.
        """

        # Over on a library's circulation manager, a short client token
        # is created for one of the patrons.
        encoder = ShortClientTokenEncoder()
        short_client_token = encoder.encode(
            self.library.short_name, self.library.shared_secret,
            "patron alias"
        )

        # Here at the library registry, we can validate the short
        # client token as authdata and create a
        # DelegatedPatronIdentifier for that patron.
        account_id, label = self.model.authdata_lookup(short_client_token)
        assert account_id.startswith('urn:uuid:0')
        eq_("Delegated account ID %s" % account_id, label)

        # The UUID corresponds to a DelegatedPatronIdentifier,
        # associated with the foreign library and the patron
        # identifier that library encoded in its JWT.
        [dpi] = self._db.query(DelegatedPatronIdentifier).all()
        eq_("patron alias", dpi.patron_identifier)
        eq_(account_id, dpi.delegated_identifier)
        eq_(self.library, dpi.library)

        # The label is the same one we get by calling urn_to_label.
        eq_(label, self.model.urn_to_label(account_id))

        # We get the same UUID and label by splitting the short client
        # token into a 'token' part and a 'signature' part, and
        # passing the token and signature to standard_lookup as
        # username and password.
        token, signature = short_client_token.rsplit('|', 1)
        credentials = dict(username=token, password=signature)
        new_account_id, new_label = self.model.standard_lookup(credentials)
        eq_(new_account_id, account_id)
        eq_(new_label, label)

    def test_short_client_token_lookup_failure(self):
        """An invalid short client token will not be turned into an
        Adobe Account ID.
        """
        eq_(
            (None, None),
            self.model.standard_lookup(
                dict(username="bad token", password="bad signature")
            )
        )

        eq_(None, None, self.model.authdata_lookup(None))
        eq_(None, None, self.model.authdata_lookup('badauthdata'))

        # This token is correctly formed but the signature doesn't match.
        encoder = ShortClientTokenEncoder()
        bad_signature = encoder.encode(
            self.library.short_name,
            self.library.shared_secret + "bad",
            "patron alias"
        )
        eq_(None, None, (self.model.authdata_lookup, bad_signature))


    def test_delegation_standard_lookup(self):
        """A model that doesn't know how to handle a login request can
        delegate to another Vendor ID server.
        """
        delegate1 = MockAdobeVendorIDClient()
        delegate2 = MockAdobeVendorIDClient()

        # Delegate 1 can't verify this user.
        delegate1.enqueue(VendorIDAuthenticationError("Nope"))

        # Delegate 2 can.
        delegate2.enqueue(("adobe_id", "label", "content"))

        delegates = [delegate1, delegate2]
        model = AdobeVendorIDModel(self._db, self.NODE_VALUE, delegates)

        # This isn't a valid Short Client Token, but as long as
        # a delegate can decode it, that doesn't matter.
        username = self.library.short_name + "|1234|someuser"

        result = model.standard_lookup(
            dict(username=username, password="password")
        )
        eq_(("adobe_id", "Delegated account ID adobe_id"), result)

        # We tried delegate 1 before getting the answer from delegate 2.
        eq_([], delegate1.queue)
        eq_([], delegate2.queue)

        # A DelegatedPatronIdentifier was created to store the information
        # we got from the delegate.
        [delegated] = self.library.delegated_patron_identifiers
        eq_("someuser", delegated.patron_identifier)
        eq_("adobe_id", delegated.delegated_identifier)
        eq_(DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID, delegated.type)

        # Now test with a username/password that's not a Short Client Token
        # at all.
        delegate1.enqueue(("adobe_id_2", "label_2", "content"))
        result = model.standard_lookup(
            dict(username="not a short client token", password="some password")
        )

        # delegate1 provided the answer, and we used it as is.
        eq_(("adobe_id_2", "label_2"), result)

        # We did not create a local DelegatedPatronIdentifier, because
        # we don't know which Library the patron should be associated
        # with.
        eq_([delegated], self.library.delegated_patron_identifiers)

    def test_delegation_authdata_lookup(self):
        """Test the ability to delegate an authdata login request
        to another server.
        """
        delegate1 = MockAdobeVendorIDClient()
        delegate2 = MockAdobeVendorIDClient()
        delegates = [delegate1, delegate2]
        model = AdobeVendorIDModel(self._db, self.NODE_VALUE, delegates)

        # First, test an authdata that is a Short Client Token.

        # Delegate 1 can verify the authdata
        delegate1.enqueue(("adobe_id", "label", "content"))

        # Delegate 2 is broken.
        delegate2.enqueue(VendorIDServerException("blah"))

        authdata = self.library.short_name + "|1234|authdatauser|password"
        result = model.authdata_lookup(authdata)
        eq_(("adobe_id", "Delegated account ID adobe_id"), result)

        # We didn't even get to delegate 2.
        eq_(1, len(delegate2.queue))

        [delegated] = self.library.delegated_patron_identifiers
        eq_("authdatauser", delegated.patron_identifier)
        eq_("adobe_id", delegated.delegated_identifier)
        eq_(DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID, delegated.type)

        # If we try it again, we'll get an error from delegate 1,
        # since nothing is queued up, and then a queued error from
        # delegate 2. Then we'll try to decode the token
        # ourselves, but since it's not a valid Short Client Token,
        # we'll get an error there, and return nothing.
        result = model.authdata_lookup(authdata)
        eq_((None, None), result)
        eq_([], delegate2.queue)

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
        eq_(("adobe_id_3", "label"), result)

        eq_([delegated], self.library.delegated_patron_identifiers)

        # We didn't get to delegate 2, because delegate 1 had the answer.
        eq_(1, len(delegate2.queue))
