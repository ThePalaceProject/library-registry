import base64
import datetime
import logging
import uuid

from jwt.algorithms import HMACAlgorithm

from library_registry.model import Library
from library_registry.model_helpers import get_one


class ShortClientTokenTool:
    ##### Class Constants ####################################################  # noqa: E266
    ALGORITHM = 'HS256'

    signer = HMACAlgorithm(HMACAlgorithm.SHA256)

    JWT_EPOCH = datetime.datetime(1970, 1, 1)   # The JWT spec takes January 1 1970 as the epoch.

    # For the sake of shortening tokens, the Short Client Token spec takes January 1 2017 as the epoch,
    # and measures time in minutes rather than seconds.
    SCT_EPOCH = datetime.datetime(2017, 1, 1)

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    ##### Private Methods ####################################################  # noqa: E266

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    @classmethod
    def adobe_base64_encode(cls, to_encode):
        """
        A modified base64 encoding that avoids triggering an Adobe bug.

        The bug seems to happen when the 'password' portion of a username/password pair contains
        a + character. So we replace + with :. We also replace / (another "suspicious" character)
        with ;. and strip newlines.
        """
        if isinstance(to_encode, str):
            to_encode = to_encode.encode("utf8")

        encoded = base64.encodebytes(to_encode)

        return encoded.replace(b"+", b":").replace(b"/", b";").replace(b"=", b"@").strip()

    @classmethod
    def adobe_base64_decode(cls, to_decode):
        """Undoes adobe_base64_encode."""
        if isinstance(to_decode, str):
            to_decode = to_decode.encode("utf8")

        to_decode = to_decode.replace(b":", b"+").replace(b";", b"/").replace(b"@", b"=")

        return base64.decodebytes(to_decode)

    @classmethod
    def sct_numericdate(cls, d):
        """
        Turn a datetime object into a number of minutes since the epoch, as per the Short Client Token spec.

        If the input datetime is before the SCT_EPOCH, return 0.
        """
        return max(int((d-cls.SCT_EPOCH).total_seconds() / 60), 0)

    @classmethod
    def jwt_numericdate(cls, d):
        """
        Turn a datetime object into a NumericDate as per RFC 7519

        If the input datetime is before the JWT_EPOCH, return 0.
        """
        return max(int((d-cls.JWT_EPOCH).total_seconds()), 0)

    ##### Private Class Methods ##############################################  # noqa: E266


class ShortClientTokenEncoder(ShortClientTokenTool):
    """
    Encode short client tokens, as per the Vendor ID Service spec:
    https://docs.google.com/document/d/1j8nWPVmy95pJ_iU4UTC-QgHK2QhDUSdQ0OQTFR2NE_0

    Used by the circulation manager. Only used by the library registry in tests.
    """
    ##### Class Constants ####################################################  # noqa: E266

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    def __init__(self):
        self.log = logging.getLogger("Short client token encoder")

    def encode(self, library_short_name, library_secret, patron_identifier):
        """
        Generate a short client token suitable for putting in an OPDS feed, where it can be picked
        up by a client and sent to a library registry to look up an Adobe ID.

        :return: A string containing a short client token.
        """
        if not library_short_name or not library_secret:
            raise ValueError("Both library short name and secret must be specified.")

        if not patron_identifier:
            raise ValueError("No patron identifier specified.")

        now = datetime.datetime.utcnow()
        expires = int(self.sct_numericdate(now + datetime.timedelta(minutes=60)))

        return self._encode(library_short_name, library_secret, patron_identifier, expires)

    ##### Private Methods ####################################################  # noqa: E266

    def _encode(self, library_short_name, library_secret, patron_identifier, expires):
        short_token_signing_key = self.signer.prepare_key(library_secret)

        base = library_short_name + "|" + str(expires) + "|" + patron_identifier
        base_bytestring = base.encode("utf8")
        signature = self.signer.sign(base_bytestring, short_token_signing_key)
        signature = self.adobe_base64_encode(signature)

        if len(base) > 80:
            msg = "Username portion of short client token exceeds 80 characters; Adobe will probably truncate it."
            self.log.error(msg)

        if len(signature) > 76:
            msg = "Password portion of short client token exceeds 76 characters; Adobe will probably truncate it."
            self.log.error(msg)

        return (base_bytestring + b"|" + signature).decode("utf8")

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    ##### Private Class Methods ##############################################  # noqa: E266


class ShortClientTokenDecoder(ShortClientTokenTool):
    """Turn a short client token into a DelegatedPatronIdentifier."""

    ##### Class Constants ####################################################  # noqa: E266

    ##### Public Interface / Magic Methods ###################################  # noqa: E266

    def uuid(self):
        """Create a new UUID URN compatible with the Vendor ID system."""
        u = str(uuid.uuid1(self.node_value))
        # This chop is required by the spec. I have no idea why, but since the first part of the
        # UUID is the least significant, it doesn't do much damage.
        value = "urn:uuid:0" + u[1:]
        return value

    def __init__(self, node_value, delegates):
        super(ShortClientTokenDecoder, self).__init__()
        if isinstance(node_value, str):
            # The node value may be stored in hex (that's how Adobe gives it out), or the equivalent decimal value.
            if node_value.startswith('0x'):
                node_value = int(node_value, 16)
            else:
                node_value = int(node_value)

        self.node_value = node_value
        self.delegates = delegates

    def decode(self, _db, token):
        """
        Decode a short client token.

        :return: a DelegatedPatronIdentifier

        :raise ValueError: When the token is not valid for any reason.
        """
        if not token:
            raise ValueError("Cannot decode an empty token.")

        if '|' not in token:
            raise ValueError(f'Supposed client token "{token}" does not contain a pipe.')

        (username, password) = token.rsplit('|', 1)
        return self.decode_two_part(_db, username, password)

    def decode_two_part(self, _db, username, password):
        """Decode a short client token that has already been split into two parts."""
        library = patron_identifier = account_id = None

        # No matter how we do this, if we're going to create a DelegatedPatronIdentifier, we need to extract
        # the Library and the library's identifier for this patron from the 'username' part of the token.
        #
        # If this username/password is not actually a Short Client Token, this will raise an exception, which
        # gives us a quick way to bail out.
        (library, _, patron_identifier) = self._split_token(_db, username)

        # First see if a delegate can give us an Adobe ID (account_id) for this patron.
        for delegate in self.delegates:
            try:
                (account_id, _, _) = delegate.sign_in_standard(username, password)
            except Exception:
                pass        # This delegate couldn't help us.

            if account_id:
                break       # We got it -- no need to keep checking delegates.

        if not account_id:  # The delegates couldn't help us; let's try to do it ourselves.
            try:
                signature = self.adobe_base64_decode(password)
            except Exception:
                raise ValueError("Invalid password: %s" % password)

            (patron_identifier, account_id) = self._decode(_db, username, signature)

        # If we got this far, we have a Library, a patron_identifier, and an account_id.
        from library_registry.model import DelegatedPatronIdentifier
        (delegated_patron_identifier, _) = DelegatedPatronIdentifier.get_one_or_create(
            _db, library, patron_identifier, DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID, account_id)

        return delegated_patron_identifier

    ##### Private Methods ####################################################  # noqa: E266

    def _split_token(self, _db, token):
        """
        Split the 'username' part of a Short Client Token.

        :return: A 3-tuple (Library, expiration, foreign patron identifier)
        """
        if token.count('|') < 2:
            raise ValueError("Invalid client token: %s" % token)

        (library_short_name, expiration, patron_identifier) = token.split("|", 2)
        library_short_name = library_short_name.upper()

        # Look up the Library object based on short name.
        library = get_one(_db, Library, short_name=library_short_name)

        if not library:
            raise ValueError("I don't know how to handle tokens from library \"%s\"" % library_short_name)

        try:
            expiration = float(expiration)
        except ValueError:
            raise ValueError('Expiration time "%s" is not numeric.' % expiration)

        return library, expiration, patron_identifier

    def _decode(self, _db, token, supposed_signature):
        """Make sure a client token is properly formatted, correctly signed, and not expired."""
        (library, expiration, patron_identifier) = self._split_token(_db, token)
        secret = library.shared_secret

        # We don't police the content of the patron identifier but there has to be _something_ there.
        if not patron_identifier:
            raise ValueError(f"Token {token} has empty patron identifier.")

        # Don't bother checking an expired token. Currently there are two ways of specifying a token's
        # expiration date: as a number of minutes since self.SCT_EPOCH or as a number of seconds since self.JWT_EPOCH.
        now = datetime.datetime.utcnow()

        # NOTE: The JWT code needs to be removed by the year 4869 or this will break.
        if expiration < 1500000000:
            # This is a number of minutes since the start of 2017.
            expiration = self.SCT_EPOCH + datetime.timedelta(minutes=expiration)
        else:
            # This is a number of seconds since the start of 1970.
            expiration = self.JWT_EPOCH + datetime.timedelta(seconds=expiration)

        if expiration < now:
            raise ValueError(f"Token {token} expired at {expiration} (now is {now}).")

        # Sign the token and check against the provided signature.
        key = self.signer.prepare_key(secret)
        token_bytes = token.encode("utf8")
        actual_signature = self.signer.sign(token_bytes, key)

        if actual_signature != supposed_signature:
            raise ValueError(f"Invalid signature for {token}.")

        # We have a Library, and a patron identifier which we know is valid.
        # Find or create a DelegatedPatronIdentifier for this person.
        return patron_identifier, self.uuid

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    ##### Private Class Methods ##############################################  # noqa: E266
