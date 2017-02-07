from nose.tools import (
    eq_,
    set_trace,
    assert_raises_regexp
)
import logging
from util.short_client_token import ShortClientTokenEncoder
from model import (
    DelegatedPatronIdentifier,
    ShortClientTokenDecoder,
)

from . import DatabaseTest

class TestShortClientToken(DatabaseTest):

    TEST_NODE_VALUE = 114740953091845
    
    def test_short_client_token_lookup_delegated_patron_identifier_success(self):
        """Test that the library registry can create a
        DelegatedPatronIdentifier from a short client token generated
        by one of its libraries.
        """
        encoder = ShortClientTokenEncoder()
        library = self._library()
        short_client_token = encoder.encode(
            library.adobe_short_name, library.adobe_shared_secret,
            "Foreign Patron"
        )

        decoder = ShortClientTokenDecoder(self.TEST_NODE_VALUE)
        identifier = decoder.decode(self._db, short_client_token)
        assert isinstance(identifier, DelegatedPatronIdentifier)
        eq_(library, identifier.library)
        eq_(DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID, identifier.type)
        eq_("Foreign Patron", identifier.patron_identifier)
        assert identifier.delegated_identifier.startswith('urn:uuid:')
        
        # Do the lookup again and verify we get the same
        # DelegatedPatronIdentifier.
        identifier2 = decoder.decode(self._db, short_client_token)
        eq_(identifier, identifier2)
        
    def test_short_client_token_lookup_delegated_patron_identifier_failure(self):
        """Test various token decoding errors"""
        decoder = ShortClientTokenDecoder(self.TEST_NODE_VALUE)
        library = self._library()
        library.adobe_short_name="LIBRARY"
        
        m = decoder._decode

        # A token has to contain at least two pipe characters.
        assert_raises_regexp(
            ValueError, "Invalid client token",
            m, self._db, "foo|", "signature"
        )
        
        # The expiration time must be numeric.
        assert_raises_regexp(
            ValueError, 'Expiration time "a time" is not numeric',
            m, self._db, "library|a time|patron", "signature"
        )

        # The patron identifier must not be blank.
        assert_raises_regexp(
            ValueError, 'Token library|1234| has empty patron identifier',
            m, self._db, "library|1234|", "signature"
        )
        
        # The library must be a known one.
        assert_raises_regexp(
            ValueError,
            'I don\'t know how to handle tokens from library "UNKNOWN"',
            m, self._db, "unknown|1234|patron", "signature"
        )

        # The token must not have expired.
        assert_raises_regexp(
            ValueError,
            'Token mylibrary|1234|patron expired at 1970-01-01 00:20:34',
            m, self._db, "library|1234|patron", "signature"
        )

        # Finally, the signature must be valid.
        assert_raises_regexp(
            ValueError, 'Invalid signature for',
            m, self._db, "library|99999999999|patron", "signature"
        )

