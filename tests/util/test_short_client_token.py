from datetime import datetime, timedelta

import pytest

from library_registry.util.short_client_token import (
    ShortClientTokenDecoder,
    ShortClientTokenEncoder,
    ShortClientTokenTool,
)
from library_registry.model import DelegatedPatronIdentifier

TEST_NODE_VALUE = 114740953091845
SCT_TESTLIB_SHORT_NAME = 'LIBRARY'
SCT_TESTLIB_SECRET = 'LIBSECRET'
GENERIC_PATRONID = 'PATRONID'


@pytest.fixture
def encoder():
    encoder_obj = ShortClientTokenEncoder()
    yield encoder_obj


@pytest.fixture
def decoder():
    decoder_obj = ShortClientTokenDecoder(TEST_NODE_VALUE, [])
    yield decoder_obj


@pytest.fixture
def sct_test_library(db_session, create_test_library):
    library_obj = create_test_library(db_session, short_name=SCT_TESTLIB_SHORT_NAME)
    library_obj.shared_secret = SCT_TESTLIB_SECRET
    yield library_obj
    db_session.delete(library_obj)
    db_session.commit()


class TestShortClientTokenTool:
    @pytest.mark.parametrize(
        'input,output',
        [
            pytest.param(b'alphabravocharliedelta', b'YWxwaGFicmF2b2NoYXJsaWVkZWx0YQ@@', id='simple_bytes'),
            pytest.param('alphabravocharliedelta', b'YWxwaGFicmF2b2NoYXJsaWVkZWx0YQ@@', id='simple_string'),
            pytest.param(chr(2110).encode('utf8'), b'4KC:', id='degree_symbol_includes_plus_sign'),
            pytest.param(chr(3647).encode('utf8'), b'4Li;', id='thai_bhat_symbol_includes_forward_slash'),
            pytest.param(chr(97).encode('utf8'), b'YQ@@', id='lowercase_a_includes_equals_sign')
        ]
    )
    def test_adobe_base64_encode(self, input, output):
        """
        GIVEN: A string or bytestring to encode
        WHEN:  ShortClientTokenTool.adobe_base64_encode() is called on that string
        THEN:  A base64 encoded bytestring should be returned with the following changes:
                - Any plus character ('+') should be replaced with a colon character (':')
                - Any forward slash character ('/') should be replaced with a semicolon (';')
                - Any equals sign character ('=') should be replaced with an at sign character ('@')
                - Newlines should be stripped

        Note that the substitutions are made in the base64 *output*, not the input string.
        """
        assert ShortClientTokenTool.adobe_base64_encode(input) == output

    @pytest.mark.parametrize(
        'input,output',
        [
            pytest.param(b'YWxwaGFicmF2b2NoYXJsaWVkZWx0YQ@@', b'alphabravocharliedelta', id='simple_bytes'),
            pytest.param(b'4KC:', chr(2110).encode('utf8'), id='degree_symbol_includes_plus_sign'),
            pytest.param(b'4Li;', chr(3647).encode('utf8'), id='thai_bhat_symbol_includes_forward_slash'),
            pytest.param(b'YQ@@', chr(97).encode('utf8'), id='lowercase_a_includes_equals_sign')
        ]
    )
    def test_adobe_base64_decode(self, input, output):
        """
        GIVEN: A bytestring encoded by ShortClientTokenTool.adobe_base64_encode
        WHEN:  ShortClientTokenTool.adobe_base64_decode() is called on that bytestring
        THEN:  After the following substitutions are performed on the input, a decoded bytestring should return:
                - Any colon character (':') should be replaced with a plus character ('+')
                - Any semicolon character (';') should be replaced with a forward slash ('/')
                - Any at sign ('@') should be replaced with an equals sign ('=')
        """
        assert ShortClientTokenTool.adobe_base64_decode(input) == output

    @pytest.mark.parametrize(
        'input,output',
        [
            pytest.param(datetime(2018, 1, 1, 12, 30, 0, 0), 526350, id='jan_1_2018'),
            pytest.param(ShortClientTokenTool.SCT_EPOCH - timedelta(days=365), 0, id='time_before_sct_epoch'),
        ]
    )
    def test_sct_numericdate(self, input, output):
        """
        GIVEN: A datetime object
        WHEN:  ShortClientTokenTool.sct_numericdate() is called on that object
        THEN:  An integer representing the number of minutes since the epoch should be returned, where
               the epoch datetime is defined in ShortClientTokenTool.SCT_EPOCH
        """
        assert ShortClientTokenTool.sct_numericdate(input) == output

    @pytest.mark.parametrize(
        'input,output',
        [
            pytest.param(datetime(2018, 1, 1, 12, 30, 0, 0), 1514809800, id='jan_1_2018'),
            pytest.param(ShortClientTokenTool.JWT_EPOCH - timedelta(days=365), 0, id='time_before_jwt_epoch'),
        ]
    )
    def test_jwt_numericdate(self, input, output):
        """
        GIVEN: A datetime object
        WHEN:  ShortClientTokenTool.jwt_numericdate() is called on that object
        THEN:  An integer representing the number of seconds since the epoch should be returned, where
               the epoch datetime is defined in ShortClientTokenTool.JWT_EPOCH
        """
        assert ShortClientTokenTool.jwt_numericdate(input) == output


class TestShortClientTokenEncoder:
    def test_encode_well_formed_result(self, encoder):
        """
        GIVEN: Three strings, representing
                - a library short name
                - a library secret
                - a patron identifier
        WHEN:  ShortClientTokenEncoder().encode() is called on those strings
        THEN:  A four part, pipe-delimited string should be returned, representing
               <LIB_SHORT_NAME>|<EXPIRY>|<PATRON_ID>|<B64_ENCODED_SIGNATURE>, where:
                - LIB_SHORT_NAME is the string passed in
                - EXPIRY is an epoch time in minutes
                - PATRON_ID is the string passed in
                - B64_ENCODED_SIGNATURE is an encoded string signed with a signing key derived
                  from the library secret passed in, which can be decoded by ShortClientTokenDecoder.decode()
        """
        lib_short_name = 'LIBSHORTNAME'
        lib_secret = 'LIBSECRET'
        patron_id = 'PATRONID'
        result = encoder.encode(lib_short_name, lib_secret, patron_id).split('|')
        assert len(result) == 4
        assert result[0] == lib_short_name
        try:
            int(result[1])
        except ValueError:
            assert False
        assert result[2] == patron_id

    def test_encode_bad_parameters(self, encoder):
        """
        GIVEN: An instance of ShortClientTokenEncoder
        WHEN:  .encode() is called with missing parameters, or None values
        THEN:  An appropriate ValueError should be raised
        """
        with pytest.raises(ValueError) as exc:
            encoder.encode(None, None, None)
        assert "Both library short name and secret must be specified." in str(exc)

        with pytest.raises(ValueError) as exc:
            encoder.encode('LIBSHORTNAME', None, None)
        assert "Both library short name and secret must be specified." in str(exc)

        with pytest.raises(ValueError) as exc:
            encoder.encode('LIBSHORTNAME', 'LIBSECRET', None)
        assert "No patron identifier specified." in str(exc)

    def test_encode_short_client_token_uses_adobe_base64_encoding(self, encoder):
        class MockSigner:
            def prepare_key(self, key):
                return key

            def sign(self, value, key):
                """Always return the same signature, crafted to contain a
                plus sign, a slash and an equal sign when base64-encoded.
                """
                return "!\tFN6~'Es52?X!#)Z*_S"

        encoder.signer = MockSigner()
        token = encoder._encode("lib", "My library secret", "1234", 0)

        # The signature part of the token has been encoded with our
        # custom encoding, not vanilla base64.
        assert token == 'lib|0|1234|IQlGTjZ:J0VzNTI;WCEjKVoqX1M@'


class TestShortClientTokenDecoder:
    def test_uuid(self, decoder):
        """
        GIVEN: An instance of ShortClientTokenDecoder
        WHEN:  The .uuid() method is called
        THEN:  A string should be returned in the format 'urn:uuid:0' + uuid, where the uuid
               value is seeded based on the node value the decoder was instantiated with.
        """
        u = decoder.uuid()
        # All UUIDs need to start with a 0 and end with the same node value.
        assert u.startswith('urn:uuid:0')
        assert u.endswith('685b35c00f05')

    def test_decode(self, db_session, encoder, decoder, sct_test_library):
        """
        GIVEN: A four part, pipe-delimited string produced by ShortClientTokenEncoder().encode(),
               based on a known shared secret, and an instance of ShortClientTokenDecoder.
        WHEN:  That bytestring is passed to the .decode() method of the ShortClientTokenDecoder instance
        THEN:  An instance of DelegatedPatronIdentifier is returned
        """
        token = encoder.encode(SCT_TESTLIB_SHORT_NAME, SCT_TESTLIB_SECRET, GENERIC_PATRONID)
        identifier = decoder.decode(db_session, token)
        assert isinstance(identifier, DelegatedPatronIdentifier)
        assert identifier.library == sct_test_library
        assert identifier.patron_identifier == GENERIC_PATRONID
        assert identifier.delegated_identifier.startswith('urn:uuid:')

        # Do the lookup again and verify we get the same DelegatedPatronIdentifier.
        identifier2 = decoder.decode(db_session, token)
        assert identifier2 == identifier

    def test_decode_two_part(self, db_session, encoder, decoder, sct_test_library):
        """
        GIVEN: A username and password derived from a short client token produced by ShortClientTokenEncoder.encode,
               and an instance of ShortClientTokenDecoder, where the username is the pipe delimited, left-most portion
               of the token, containing '<LIBRARY_SHORT_NAME>|<EXPIRY>|<PATRON_ID>' and the password is the signature
               portion of the token.
        WHEN:  The username and password are passed to the .decode_two_part() method of the ShortClientTokenDecoder
        THEN:  An instance of DelegatedPatronIdentifier is returned
        """
        token = encoder.encode(SCT_TESTLIB_SHORT_NAME, SCT_TESTLIB_SECRET, GENERIC_PATRONID)
        (username, password) = token.rsplit('|', 1)
        identifier = decoder.decode_two_part(db_session, username, password)
        assert isinstance(identifier, DelegatedPatronIdentifier)
        assert identifier.library == sct_test_library
        assert identifier.patron_identifier == GENERIC_PATRONID
        assert identifier.delegated_identifier.startswith('urn:uuid:')

        # Do the lookup again and verify we get the same DelegatedPatronIdentifier.
        identifier2 = decoder.decode(db_session, token)
        assert identifier2 == identifier

    def test__split_token_bad_parameter(self, db_session, decoder, sct_test_library):
        """
        GIVEN: A corrupt or missing short client token string and an instance of ShortClientTokenDecoder
        WHEN:  The string is passed to the ._split_token() method of the ShortClientTokenDecoder instance
        THEN:  An appropriate exception should be raised
        """
        # A token has to contain at least two pipe characters.
        with pytest.raises(ValueError) as exc:
            decoder._split_token(db_session, "foo|")
        assert "Invalid client token" in str(exc.value)

        # A library with the short name obtained from the token must exist
        nonexistent_library = "NONEXISTENT_LIBRARY"
        with pytest.raises(ValueError) as exc:
            decoder._split_token(db_session, f"{nonexistent_library}|12345|patron")
        assert f'I don\'t know how to handle tokens from library "{nonexistent_library}"' in str(exc.value)

        # The expiration time must be numeric.
        with pytest.raises(ValueError) as exc:
            decoder._split_token(db_session, f"{sct_test_library.short_name}|a time|patron")
        assert 'Expiration time "a time" is not numeric' in str(exc.value)

    @pytest.mark.skip(reason="TODO")
    def test_decode_two_part_bad_parameters(self):
        """
        GIVEN: A short client token with a signature that cannot be decoded by any delegate or by
               ShortClientTokenTool.adobe_base64_decode().
        WHEN:  ShortClientTokenDecoder.decode_two_part() is called with that signature
        THEN:  An exception should be raised
        """

    @pytest.mark.skip(reason="TODO")
    def test__decode(self):
        """
        GIVEN: A valid short client token / signature and an instance of ShortClientTokenDecoder
        WHEN:  The ._decode() method is called on that token and signature
        THEN:  A DelegatedPatronIdentifier instance should be returned
        """

    def test__decode_bad_parameters(self, db_session, decoder, sct_test_library):
        """
        GIVEN: A corrupt or missing token string and an instance of ShortClientTokenDecoder
        WHEN:  That string is passed to the ._decode() method of the ShortClientTokenDecoder instance
        THEN:  An appropriate exception should be raised
        """
        # The patron identifier must not be blank.
        with pytest.raises(ValueError) as exc:
            decoder._decode(db_session, f"{sct_test_library.short_name}|1234|", "signature")
        assert f'Token {sct_test_library.short_name}|1234| has empty patron identifier' in str(exc.value)

        # The token must not have expired.
        with pytest.raises(ValueError) as exc:
            decoder._decode(db_session, f"{sct_test_library.short_name}|1234|patron", "signature")
        assert f'Token {sct_test_library.short_name}|1234|patron expired at 2017-01-01 20:34:00' in str(exc.value)

        # (Even though the expiration number here is much higher, this token is also expired, because
        # the expiration date calculation for an old-style token starts at a different epoch and treats
        # the expiration number as seconds rather than minutes.)
        with pytest.raises(ValueError) as exc:
            decoder._decode(db_session, f"{sct_test_library.short_name}|1500000000|patron", "signature")
        assert f'Token {sct_test_library.short_name}|1500000000|patron expired at 2017-07-14 02:40:00' in str(exc.value)

        # Finally, the signature must be valid.
        with pytest.raises(ValueError) as exc:
            decoder._decode(db_session, f"{sct_test_library.short_name}|99999999999|patron", "signature")
        assert 'Invalid signature for' in str(exc.value)

    def test_decode_bad_parameter(self, db_session, decoder):
        """
        GIVEN: A missing or corrupted token and an instance of ShortClientTokenDecoder
        WHEN:  The token is passed to the .decoder() method of the ShortClientTokenDecoder instance
        THEN:  An appropriate exception should be raised
        """
        with pytest.raises(ValueError) as exc:
            decoder.decode(db_session, "")
        assert 'Cannot decode an empty token.' in str(exc.value)

        with pytest.raises(ValueError) as exc:
            decoder.decode(db_session, "no pipes")
        assert 'Supposed client token "no pipes" does not contain a pipe.' in str(exc.value)

        # The library must be a known one.
        with pytest.raises(ValueError) as exc:
            decoder._decode(db_session, "unknown|1234|patron", "signature")
        assert 'I don\'t know how to handle tokens from library "UNKNOWN"' in str(exc.value)
