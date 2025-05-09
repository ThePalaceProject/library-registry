"""Test the helper objects in util.string."""

import base64 as stdlib_base64
import re

import pytest

from util.string_helpers import UnicodeAwareBase64, base64, random_string


class TestUnicodeAwareBase64:
    def test_encoding(self):
        test_string = "םולש"

        # Run the same tests against two different encodings that can
        # handle Hebrew characters.
        self._test_encoder(test_string, UnicodeAwareBase64("utf8"))
        self._test_encoder(test_string, UnicodeAwareBase64("iso-8859-8"))

        # If UnicodeAwareBase64 is given a string it can't encode in
        # its chosen encoding, an exception is the result.
        shift_jis = UnicodeAwareBase64("shift-jis")
        with pytest.raises(UnicodeEncodeError):
            shift_jis.b64decode(test_string)

    def _test_encoder(self, test_string, base64):
        # Create a binary version of the string in the encoder's
        # encoding, for use in comparisons.
        binary = test_string.encode(base64.encoding)

        # Test all supported methods of the base64 API.
        for encode, decode in [
            ("b64encode", "b64decode"),
            ("standard_b64encode", "standard_b64decode"),
            ("urlsafe_b64encode", "urlsafe_b64decode"),
        ]:
            encode_method = getattr(base64, encode)
            decode_method = getattr(base64, decode)

            # Test a round-trip. Base64-encoding a Unicode string and
            # then decoding it should give the original string.
            encoded = encode_method(test_string)
            decoded = decode_method(encoded)
            assert decoded == test_string

            # Test encoding on its own. Encoding with a
            # UnicodeAwareBase64 and then converting to ASCII should
            # give the same result as running the binary
            # representation of the string through the default bas64
            # module.
            base_encode = getattr(stdlib_base64, encode)
            base_encoded = base_encode(binary)
            assert base_encoded == encoded.encode("ascii")

            # If you pass in a bytes object to a UnicodeAwareBase64
            # method, it's no problem. You get a Unicode string back.
            assert encode_method(binary) == encoded
            assert decode_method(base_encoded) == decoded

    def test_default_is_base64(self):
        # If you import "base64" from util.string, you get a
        # UnicodeAwareBase64 object that encodes as UTF-8 by default.
        assert isinstance(base64, UnicodeAwareBase64)
        assert base64.encoding == "utf8"
        snowman = "☃"
        snowman_utf8 = snowman.encode("utf8")
        as_base64 = base64.b64encode(snowman)
        assert as_base64 == "4piD"

        # This is a Unicode representation of the string you'd get if
        # you encoded the snowman as UTF-8, then used the standard
        # library to base64-encode the bytestring.
        assert stdlib_base64.b64encode(snowman_utf8) == b"4piD"


class TestRandomstring:
    def test_random_string(self):
        assert random_string(0) == ""

        # The strings are random.
        res1 = random_string(8)
        res2 = random_string(8)
        assert res1 != res2

        # We can't test exact values, because the randomness comes
        # from /dev/urandom, but we can test some of their properties:
        for size in range(1, 16):
            x = random_string(size)

            # The strings are Unicode strings, not bytestrings
            assert isinstance(x, str)

            # The strings are entirely composed of lowercase hex digits.
            assert re.compile("[^a-f0-9]").search(x) is None

            # Each byte is represented as two digits, so the length of the
            # string is twice the length passed in to the function.
            assert len(x) == size * 2
