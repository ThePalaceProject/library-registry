import base64

import pytest

from palace.registry.sqlalchemy.model.delegated_patron_identifier import (
    DelegatedPatronIdentifier,
    ShortClientTokenDecoder,
)
from palace.registry.util.short_client_token import ShortClientTokenEncoder

from .fixtures.database import DatabaseTransactionFixture


class TestShortClientTokenEncoder:
    def setup_method(self):
        self.encoder = ShortClientTokenEncoder()

    def test_adobe_base64_encode_decode(self):
        # Test our special variant of base64 encoding designed to avoid
        # triggering an Adobe bug.
        value = b"!\tFN6~'Es52?X!#)Z*_S"

        encoded = self.encoder.adobe_base64_encode(value)
        assert encoded == b"IQlGTjZ:J0VzNTI;WCEjKVoqX1M@"

        # This is like normal base64 encoding, but with a colon
        # replacing the plus character, a semicolon replacing the
        # slash, an at sign replacing the equal sign and the final
        # newline stripped.
        assert (
            base64.encodebytes(value)
            == encoded.replace(b":", b"+").replace(b";", b"/").replace(b"@", b"=")
            + b"\n"
        )

        # We can reverse the encoding to get the original value.
        assert self.encoder.adobe_base64_decode(encoded) == value

    def test_encode_short_client_token_uses_adobe_base64_encoding(self):
        class MockSigner:
            def prepare_key(self, key):
                return key

            def sign(self, value, key):
                """Always return the same signature, crafted to contain a
                plus sign, a slash and an equal sign when base64-encoded.
                """
                return "!\tFN6~'Es52?X!#)Z*_S"

        self.encoder.signer = MockSigner()
        token = self.encoder._encode("lib", "My library secret", "1234", 0)

        # The signature part of the token has been encoded with our
        # custom encoding, not vanilla base64.
        assert token == "lib|0|1234|IQlGTjZ:J0VzNTI;WCEjKVoqX1M@"

    def test_must_provide_library_information(self):
        error = "Both library short name and secret must be specified."
        with pytest.raises(ValueError) as exc:
            self.encoder.encode(None, None, None)
        assert error in str(exc.value)

        with pytest.raises(ValueError) as exc:
            self.encoder.encode("A", None, None)
        assert error in str(exc.value)

        with pytest.raises(ValueError) as exc:
            self.encoder.encode(None, "A", None)
        assert error in str(exc.value)

    def test_cannot_encode_null_patron_identifier(self):
        with pytest.raises(ValueError) as exc:
            self.encoder.encode("lib", "My library secret", None)
        assert "No patron identifier specified" in str(exc.value)

    def test_short_client_token_encode_known_value(self):
        """Verify that the encoding algorithm gives a known value on known
        input.
        """
        secret = "My library secret"
        value = self.encoder._encode("a library", secret, "a patron identifier", 1234.5)

        # Note the colon characters that replaced the plus signs in
        # what would otherwise be normal base64 text. Similarly for
        # the semicolon which replaced the slash, and the at sign which
        # replaced the equals sign.
        assert (
            value
            == "a library|1234.5|a patron identifier|YoNGn7f38mF531KSWJ;o1H0Z3chbC:uTE:t7pAwqYxM@"
        )

        # Dissect the known value to show how it works.
        token, signature = value.rsplit("|", 1)

        # Signature is base64-encoded in a custom way that avoids
        # triggering an Adobe bug ; token is not.
        signature = self.encoder.adobe_base64_decode(signature)

        # The token comes from the library name, the patron identifier,
        # and the time of creation.
        assert token == "a library|1234.5|a patron identifier"

        # The signature comes from signing the token with the
        # secret associated with this library.
        key = self.encoder.signer.prepare_key(secret)
        expect_signature = self.encoder.signer.sign(token.encode("utf8"), key)
        assert signature == expect_signature


class ShortClientTokenDecoderFixture:

    TEST_NODE_VALUE = 114740953091845

    def __init__(self, db: DatabaseTransactionFixture):
        self.db = db
        self.encoder = ShortClientTokenEncoder()
        self.decoder = ShortClientTokenDecoder(self.TEST_NODE_VALUE, [])
        self.library = db.library()
        self.library.short_name = "LIBRARY"
        self.library.shared_secret = "My shared secret"


@pytest.fixture(scope="function")
def decoder_fixture(db: DatabaseTransactionFixture) -> ShortClientTokenDecoderFixture:
    return ShortClientTokenDecoderFixture(db)


class TestShortClientTokenDecoder:
    def test_uuid(self, decoder_fixture: ShortClientTokenDecoderFixture):
        u = decoder_fixture.decoder.uuid()
        # All UUIDs need to start with a 0 and end with the same node
        # value.
        assert u.startswith("urn:uuid:0")
        assert u.endswith("685b35c00f05")

    def test_short_client_token_lookup_delegated_patron_identifier_success(
        self, decoder_fixture: ShortClientTokenDecoderFixture
    ):
        """Test that the library registry can create a
        DelegatedPatronIdentifier from a short client token generated
        by one of its libraries.
        """
        short_client_token = decoder_fixture.encoder.encode(
            decoder_fixture.library.short_name,
            decoder_fixture.library.shared_secret,
            "Foreign Patron",
        )

        identifier = decoder_fixture.decoder.decode(
            decoder_fixture.db.session, short_client_token
        )
        assert isinstance(identifier, DelegatedPatronIdentifier)
        assert identifier.library == decoder_fixture.library
        assert identifier.type == DelegatedPatronIdentifier.ADOBE_ACCOUNT_ID
        assert identifier.patron_identifier == "Foreign Patron"
        assert identifier.delegated_identifier.startswith("urn:uuid:")

        # Do the lookup again and verify we get the same
        # DelegatedPatronIdentifier.
        identifier2 = decoder_fixture.decoder.decode(
            decoder_fixture.db.session, short_client_token
        )
        assert identifier2 == identifier

    def test_short_client_token_lookup_delegated_patron_identifier_failure(
        self, decoder_fixture: ShortClientTokenDecoderFixture
    ):
        """Test various token decoding errors"""
        m = decoder_fixture.decoder._decode

        with pytest.raises(ValueError) as exc:
            decoder_fixture.decoder.decode(decoder_fixture.db.session, "")
        assert "Cannot decode an empty token." in str(exc.value)

        with pytest.raises(ValueError) as exc:
            decoder_fixture.decoder.decode(decoder_fixture.db.session, "no pipes")
        assert 'Supposed client token "no pipes" does not contain a pipe.' in str(
            exc.value
        )

        # A token has to contain at least two pipe characters.
        with pytest.raises(ValueError) as exc:
            m(decoder_fixture.db.session, "foo|", "signature")
        assert "Invalid client token" in str(exc.value)

        # The expiration time must be numeric.
        with pytest.raises(ValueError) as exc:
            m(decoder_fixture.db.session, "library|a time|patron", "signature")
        assert 'Expiration time "a time" is not numeric' in str(exc.value)

        # The patron identifier must not be blank.
        with pytest.raises(ValueError) as exc:
            m(decoder_fixture.db.session, "library|1234|", "signature")
        assert r"Token library|1234| has empty patron identifier" in str(exc.value)

        # The library must be a known one.
        with pytest.raises(ValueError) as exc:
            m(decoder_fixture.db.session, "unknown|1234|patron", "signature")
        assert 'I don\'t know how to handle tokens from library "UNKNOWN"' in str(
            exc.value
        )

        # The token must not have expired.
        with pytest.raises(ValueError) as exc:
            m(decoder_fixture.db.session, "library|1234|patron", "signature")
        assert r"Token library|1234|patron expired at 2017-01-01 20:34:00" in str(
            exc.value
        )

        # (Even though the expiration number here is much higher, this
        # token is also expired, because the expiration date
        # calculation for an old-style token starts at a different
        # epoch and treats the expiration number as seconds rather
        # than minutes.)
        with pytest.raises(ValueError) as exc:
            m(decoder_fixture.db.session, "library|1500000000|patron", "signature")
        assert r"Token library|1500000000|patron expired at 2017-07-14 02:40:00" in str(
            exc.value
        )

        # Finally, the signature must be valid.
        with pytest.raises(ValueError) as exc:
            m(decoder_fixture.db.session, "library|99999999999|patron", "signature")
        assert "Invalid signature for" in str(exc.value)

    def test_decode_uses_adobe_base64_encoding(
        self, decoder_fixture: ShortClientTokenDecoderFixture
    ):

        decoder_fixture.db.library()

        # The base64 encoding of this signature has a plus sign in it.
        signature = b"LbU}66%\\-4zt>R>_)\n2Q"
        encoded_signature = decoder_fixture.encoder.adobe_base64_encode(signature)

        # We replace the plus sign with a colon.
        assert b":" in encoded_signature
        assert b"+" not in encoded_signature

        # Make sure that decode properly reverses that change when
        # decoding the 'password'.
        def _decode(_db, token, supposed_signature):
            assert supposed_signature == signature
            decoder_fixture.decoder.test_code_ran = True
            return "identifier", "uuid"

        decoder_fixture.decoder._decode = _decode

        decoder_fixture.decoder.test_code_ran = False

        # This username is good enough to fool
        # ShortClientDecoder._split_token, but it won't work for real.
        fake_username = "library|12345|username"
        decoder_fixture.decoder.decode_two_part(
            decoder_fixture.db.session, fake_username, encoded_signature
        )

        # The code in _decode_short_client_token ran. Since there was no
        # test failure, it ran successfully.
        assert decoder_fixture.decoder.test_code_ran is True

        with pytest.raises(ValueError) as exc:
            decoder_fixture.decoder.decode_two_part(
                decoder_fixture.db.session,
                fake_username,
                "I am not a real encoded signature",
            )
        assert "Invalid password" in str(exc.value)
