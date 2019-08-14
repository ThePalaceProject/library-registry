import datetime
import logging
from jwt.algorithms import HMACAlgorithm
#from util.string_helpers import base64
import base64

class ShortClientTokenTool(object):

    ALGORITHM = 'HS256'
    signer = HMACAlgorithm(HMACAlgorithm.SHA256)

    @classmethod
    def adobe_base64_encode(cls, to_encode):
        """A modified base64 encoding that avoids triggering an Adobe bug.

        The bug seems to happen when the 'password' portion of a
        username/password pair contains a + character. So we replace +
        with :. We also replace / (another "suspicious" character)
        with ;. and strip newlines.
        """
        if isinstance(to_encode, unicode):
            to_encode = to_encode.encode("utf8")
        encoded = base64.encodestring(to_encode)
        return encoded.replace(b"+", b":").replace(b"/", b";").replace(b"=", b"@").strip()

    @classmethod
    def adobe_base64_decode(cls, to_decode):
        """Undoes adobe_base64_encode."""
        if isinstance(to_decode, unicode):
            to_decode = to_decode.encode("utf8")        
        to_decode = to_decode.replace(b":", b"+").replace(b";", b"/").replace(b"@", b"=")
        return base64.decodestring(to_decode)

    # The JWT spec takes January 1 1970 as the epoch.
    JWT_EPOCH = datetime.datetime(1970, 1, 1)

    # For the sake of shortening tokens, the Short Client Token spec
    # takes January 1 2017 as the epoch, and measures time in minutes
    # rather than seconds.
    SCT_EPOCH = datetime.datetime(2017, 1, 1)

    @classmethod
    def sct_numericdate(cls, d):
        """Turn a datetime object into a number of minutes since the epoch, as
        per the Short Client Token spec.
        """
        return (d-cls.SCT_EPOCH).total_seconds() / 60

    @classmethod
    def jwt_numericdate(cls, d):
        """Turn a datetime object into a NumericDate as per RFC 7519."""
        return (d-cls.JWT_EPOCH).total_seconds()
    

class ShortClientTokenEncoder(ShortClientTokenTool):

    """Encode short client tokens, as per the
    Vendor ID Service spec:
    https://docs.google.com/document/d/1j8nWPVmy95pJ_iU4UTC-QgHK2QhDUSdQ0OQTFR2NE_0

    Used by the circulation manager. Only used by the library registry
    in tests.
    """
      
    def __init__(self):
        self.log = logging.getLogger("Short client token encoder")
    
    def encode(self, library_short_name, library_secret, patron_identifier):
        """Generate a short client token suitable for putting in an OPDS feed,
        where it can be picked up by a client and sent to a library
        registry to look up an Adobe ID.

        :return: A string containing a short client token.
        """
        if not library_short_name or not library_secret:
            raise ValueError(
                "Both library short name and secret must be specified."
            )

        if not patron_identifier:
            raise ValueError("No patron identifier specified.")
        
        now = datetime.datetime.utcnow()
        expires = int(self.sct_numericdate(now + datetime.timedelta(minutes=60)))
        return self._encode(library_short_name, library_secret,
                            patron_identifier, expires)
    
    def _encode(self, library_short_name, library_secret, patron_identifier,
                expires):
        short_token_signing_key = self.signer.prepare_key(library_secret)
        
        base = library_short_name + "|" + str(expires) + "|" + patron_identifier
        base_bytestring = base.encode("utf8")
        signature = self.signer.sign(base_bytestring, short_token_signing_key)
        signature = self.adobe_base64_encode(signature)
        if len(base) > 80:
            self.log.error(
                "Username portion of short client token exceeds 80 characters; Adobe will probably truncate it."
            )
        if len(signature) > 76:
            self.log.error(
                "Password portion of short client token exceeds 76 characters; Adobe will probably truncate it."
            )
        return (base_bytestring + b"|" + signature).decode("utf8")
