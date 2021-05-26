import re

import requests
from flask import Response, request

import adobe_xml_templates as t
from model import ShortClientTokenDecoder
from util.string_helpers import base64
from util.xmlparser import XMLParser


class AdobeVendorIDController:
    """
    Flask controllers that implement the Account Service and Authorization Service
    portions of the Adobe Vendor ID protocol.
    """
    def __init__(self, _db, vendor_id, node_value, delegates=None):
        """Constructor.

        :param delegates: A list of URLs or AdobeVendorIDClient objects. If this Vendor ID
                          server cannot validate an incoming login, it will delegate to each
                          of these other servers in turn.
        """
        if not delegates:
            delegates = []

        self._db = _db
        self.request_handler = AdobeVendorIDRequestHandler(vendor_id)
        self.model = AdobeVendorIDModel(self._db, node_value, delegates)

    def signin_handler(self):
        """Process an incoming signInRequest document."""
        __transaction = self._db.begin_nested()
        output = self.request_handler.handle_signin_request(
            request.data.decode('utf8'),
            self.model.standard_lookup,
            self.model.authdata_lookup
        )
        __transaction.commit()

        return Response(output, 200, {"Content-Type": "application/xml"})

    def userinfo_handler(self):
        """Process an incoming userInfoRequest document."""
        output = self.request_handler.handle_accountinfo_request(
            request.data.decode('utf8'),
            self.model.urn_to_label
        )
        return Response(output, 200, {"Content-Type": "application/xml"})

    def status_handler(self):
        return Response("UP", 200, {"Content-Type": "text/plain"})


class AdobeRequestParser(XMLParser):

    NAMESPACES = {"adept": "http://ns.adobe.com/adept"}

    def process(self, data):
        requests = list(self.process_all(data, self.REQUEST_XPATH, self.NAMESPACES))

        if not requests:
            return None

        return requests[0]  # Return only the first request tag, even if there are multiple

    def _add(self, d, tag, key, namespaces, transform=None):
        v = self._xpath1(tag, 'adept:' + key, namespaces)

        if v is not None:
            v = v.text
            if v is not None:
                v = v.strip()
                if callable(transform):
                    v = transform(v)

        d[key] = v


class AdobeSignInRequestParser(AdobeRequestParser):
    REQUEST_XPATH = "/adept:signInRequest"
    STANDARD = 'standard'
    AUTH_DATA = 'authData'

    def process_one(self, tag, namespaces):
        method = tag.attrib.get('method')

        if not method:
            raise ValueError("No signin method specified")

        data = dict(method=method)

        if method == self.STANDARD:
            self._add(data, tag, 'username', namespaces)
            self._add(data, tag, 'password', namespaces)
        elif method == self.AUTH_DATA:
            self._add(data, tag, self.AUTH_DATA, namespaces, base64.b64decode)
        else:
            raise ValueError(f"Unknown signin method: {method}")

        return data


class AdobeAccountInfoRequestParser(AdobeRequestParser):
    REQUEST_XPATH = "/adept:accountInfoRequest"

    def process_one(self, tag, namespaces):
        method = tag.attrib.get('method')
        data = dict(method=method)
        self._add(data, tag, 'user', namespaces)
        return data


class AdobeVendorIDRequestHandler:
    """Standalone class that can be tested without bringing in Flask or the database schema"""

    ##### Class Constants ####################################################  # noqa: E266
    AUTH_ERROR_TYPE         = "AUTH"                                        # noqa: E221
    ACCOUNT_INFO_ERROR_TYPE = "ACCOUNT_INFO"                                # noqa: E221
    TOKEN_FAILURE           = 'Incorrect token.'                            # noqa: E221
    AUTHENTICATION_FAILURE  = 'Incorrect barcode or PIN.'                   # noqa: E221
    URN_LOOKUP_FAILURE      = "Could not identify patron from '%s'."        # noqa: E221

    SIGN_IN_RESPONSE_TEMPLATE       = t.SIGN_IN_RESPONSE_TEMPLATE           # noqa: E221
    ACCOUNT_INFO_RESPONSE_TEMPLATE  = t.ACCOUNT_INFO_RESPONSE_TEMPLATE      # noqa: E221
    ERROR_RESPONSE_TEMPLATE         = t.ERROR_RESPONSE_TEMPLATE             # noqa: E221

    ##### Public Interface / Magic Methods ###################################  # noqa: E266
    def __init__(self, vendor_id):
        self.vendor_id = vendor_id

    def handle_signin_request(self, data, standard_lookup, authdata_lookup):
        parser = AdobeSignInRequestParser()

        try:
            data = parser.process(data)
        except Exception as e:
            return self.error_document(self.AUTH_ERROR_TYPE, str(e))

        user_id = label = None

        if not data:
            return self.error_document(self.AUTH_ERROR_TYPE, "Request document in wrong format.")

        if 'method' not in data:
            return self.error_document(self.AUTH_ERROR_TYPE, "No method specified")

        if data['method'] == parser.STANDARD:
            (user_id, label) = standard_lookup(data)
            failure = self.AUTHENTICATION_FAILURE
        elif data['method'] == parser.AUTH_DATA:
            authdata = data[parser.AUTH_DATA]
            (user_id, label) = authdata_lookup(authdata)
            failure = self.TOKEN_FAILURE

        if user_id is None:
            return self.error_document(self.AUTH_ERROR_TYPE, failure)
        else:
            return self.SIGN_IN_RESPONSE_TEMPLATE % {"user": user_id, "label": label}

    def handle_accountinfo_request(self, data, urn_to_label):
        parser = AdobeAccountInfoRequestParser()
        label = None

        try:
            data = parser.process(data)
            if not data:
                return self.error_document(self.ACCOUNT_INFO_ERROR_TYPE, "Request document in wrong format.")

            if 'user' not in data:
                return self.error_document(self.ACCOUNT_INFO_ERROR_TYPE,
                                           "Could not find user identifer in request document.")

            label = urn_to_label(data['user'])
        except Exception as e:
            return self.error_document(self.ACCOUNT_INFO_ERROR_TYPE, str(e))

        if label:
            return self.ACCOUNT_INFO_RESPONSE_TEMPLATE % dict(label=label)
        else:
            return self.error_document(self.ACCOUNT_INFO_ERROR_TYPE, self.URN_LOOKUP_FAILURE % data['user'])

    def error_document(self, type, message):
        return self.ERROR_RESPONSE_TEMPLATE % {"vendor_id": self.vendor_id, "type": type, "message": message}

    ##### Private Methods ####################################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    ##### Private Class Methods ##############################################  # noqa: E266


class AdobeVendorIDModel:
    """Implement Adobe Vendor ID within the library registry's database model"""

    def __init__(self, _db, node_value, delegates):
        self._db = _db
        delegate_objs = []

        for i in delegates:
            if isinstance(i, str):
                delegate_objs.append(AdobeVendorIDClient(i))
            else:
                delegate_objs.append(i)

        self.short_client_token_decoder = ShortClientTokenDecoder(node_value, delegate_objs)

    def standard_lookup(self, authorization_data):
        """
        Treat an incoming username and password as the two parts of a short client token.
        Return an Adobe Account ID and a human-readable label. Create a DelegatedPatronIdentifier
        to hold the Adobe Account ID if necessary.
        """
        username = authorization_data.get('username')
        password = authorization_data.get('password')

        try:
            delegated_patron_identifier = self.short_client_token_decoder.decode_two_part(
                self._db, username, password
            )
        except ValueError:
            delegated_patron_identifier = None

        if delegated_patron_identifier:
            return self.account_id_and_label(delegated_patron_identifier)
        else:
            for delegate in self.short_client_token_decoder.delegates:
                try:
                    (account_id, label, _) = delegate.sign_in_standard(username, password)
                    return account_id, label
                except Exception:
                    pass        # This delegate couldn't help us.

        return (None, None)   # Neither this server nor the delegates were able to do anything.

    def authdata_lookup(self, authdata):
        """
        Treat an authdata string as a short client token. Return an Adobe Account ID and a
        human-readable label. Create a DelegatedPatronIdentifier to hold the Adobe Account ID
        if necessary.
        """
        try:
            delegated_patron_identifier = self.short_client_token_decoder.decode(self._db, authdata)
        except ValueError:
            delegated_patron_identifier = None

        if delegated_patron_identifier:
            return self.account_id_and_label(delegated_patron_identifier)
        else:
            for delegate in self.short_client_token_decoder.delegates:
                try:
                    (account_id, label, _) = delegate.sign_in_authdata(authdata)
                    return account_id, label
                except Exception:
                    pass    # This delegate couldn't help us.

        return (None, None)  # Neither this server nor the delegates were able to do anything.

    def account_id_and_label(self, delegated_patron_identifier):
        """Turn a DelegatedPatronIdentifier into a 2-tuple of (account id, label)"""
        if not delegated_patron_identifier:
            return (None, None)

        urn = delegated_patron_identifier.delegated_identifier
        return (urn, self.urn_to_label(urn))

    def urn_to_label(self, urn):
        """We have no information about patrons, so labels are sparse."""
        return f"Delegated account ID {urn}"


class VendorIDAuthenticationError(Exception):
    """The Vendor ID service is working properly but returned an error."""


class VendorIDServerException(Exception):
    """The Vendor ID service is not working properly."""


class AdobeVendorIDClient:
    """
    A client library for the Adobe Vendor ID protocol.

    Used by the AdobeVendorIDAcceptanceTestScript to verify the compliance of the library registry.

    It may also be used during a transition period where you are moving from another Vendor ID
    implementation to a library registry. You can delegate to another Vendor ID implementation the
    validation of any credentials that cannot be validated through the library registry.
    """

    SIGNIN_AUTHDATA_BODY = """<signInRequest method="authData" xmlns="http://ns.adobe.com/adept">
<authData>%s</authData>
</signInRequest>"""

    SIGNIN_STANDARD_BODY = """<signInRequest method="standard" xmlns="http://ns.adobe.com/adept">
<username>%s</username>
<password>%s</password>
</signInRequest>"""

    USER_INFO_BODY = """<accountInfoRequest method="standard" xmlns="http://ns.adobe.com/adept">
<user>%s</user>
</accountInfoRequest>"""

    USER_IDENTIFIER_RE = re.compile("<user>([^<]+)</user>")
    LABEL_RE = re.compile("<label>([^<]+)</label>")
    ERROR_RE = re.compile('<error [^<]+ data="([^<]+)"')

    def __init__(self, base_url):
        self.base_url = base_url
        self.signin_url = base_url + "SignIn"
        self.accountinfo_url = base_url + "AccountInfo"
        self.status_url = base_url + "Status"

    def status(self):
        """Is the server up and running?"""
        response = requests.get(self.status_url)
        content = response.content
        self.handle_error(response.status_code, content)
        if content == 'UP':
            return True
        raise VendorIDServerException("Unexpected response: %s" % content)

    def sign_in_authdata(self, authdata):
        """Attempt to sign in using authdata.

        :param: If signin is successful, a 2-tuple (account identifier, label).
        """
        body = self.SIGNIN_AUTHDATA_BODY % base64.encodestring(authdata)
        response = requests.post(self.signin_url, data=body)
        return self._process_sign_in_result(response)

    def sign_in_standard(self, username, password):
        """Attempt to sign in using username and password."""
        body = self.SIGNIN_STANDARD_BODY % (username, password)
        response = requests.post(self.signin_url, data=body)
        return self._process_sign_in_result(response)

    def user_info(self, urn):
        """Turn a user identifier into a label."""
        body = self.USER_INFO_BODY % urn
        response = requests.post(self.accountinfo_url, data=body)
        content = response.content
        self.handle_error(response.status_code, content)
        label = self.extract_label(content)
        if not label:
            raise VendorIDServerException("Unexpected response: %s" % content)
        return label, content

    def extract_user_identifier(self, content):
        return self._extract_by_re(content, self.USER_IDENTIFIER_RE)

    def extract_label(self, content):
        return self._extract_by_re(content, self.LABEL_RE)

    def handle_error(self, status_code, content):
        if status_code != 200:
            raise VendorIDServerException(f"Unexpected status code: {status_code}")

        error = self._extract_by_re(content, self.ERROR_RE)

        if error:
            raise VendorIDAuthenticationError(error)

    def _extract_by_re(self, content, re):
        match = re.search(content)
        if not match:
            return None
        return match.groups()[0]

    def _process_sign_in_result(self, response):
        content = response.content
        self.handle_error(response.status_code, content)
        identifier = self.extract_user_identifier(content)
        label = self.extract_label(content)

        if not identifier or not label:
            raise VendorIDServerException("Unexpected response: %s" % content)

        return identifier, label, content
