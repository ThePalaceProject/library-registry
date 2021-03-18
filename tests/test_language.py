import pytest

from library_registry.util.language import LanguageCodes


class TestLanguageCodes:
    @pytest.mark.parametrize(
        "two_letter_code,three_letter_code", [
            ("en", "eng"), ("es", "spa"), ("zh", "chi")
        ]
    )
    def test_two_to_three(self, two_letter_code, three_letter_code):
        """
        GIVEN: A reference to the LanguageCodes class
        WHEN:  The attribute 'two_to_three' is accessed as a dict with a two letter key
        THEN:  The corresponding three letter language code will be returned
        """
        assert LanguageCodes.two_to_three[two_letter_code] == three_letter_code
        assert LanguageCodes.two_to_three["nosuchlanguage"] is None

    @pytest.mark.parametrize(
        "two_letter_code,three_letter_code", [
            ("en", "eng"), ("es", "spa"), ("zh", "chi")
        ]
    )
    def test_three_to_two(self, two_letter_code, three_letter_code):
        """
        GIVEN: A reference to the LanguageCodes class
        WHEN:  The attribute 'three_to_two' is accessed with a dict with a three letter key
        THEN:  The corresponding two letter language code will be returned
        """
        assert LanguageCodes.three_to_two[three_letter_code] == two_letter_code
        assert LanguageCodes.three_to_two["nosuchlanguage"] is None

    @pytest.mark.parametrize(
        "key_name,eng_name_value", [
            ("en", ["English"]),
            ("spa", ["Spanish", "Castilian"]),
            ("es", ["Spanish", "Castilian"]),
            ("zh", ["Chinese"]),
            ("chi", ["Chinese"]),
            ("nosuchlanguage", [])
        ]
    )
    def test_english_names(self, key_name, eng_name_value):
        """
        GIVEN: A reference to the LanguageCodes class
        WHEN:  The attribute 'english_names' is accessed as a dict with two or three letter keys
        THEN:  The corresponding English name for the referenced language is returned
        """
        assert LanguageCodes.english_names[key_name] == eng_name_value

    @pytest.mark.parametrize(
        "key_name,native_name_value", [
            ("en", ["English"]),
            ("eng", ["English"]),
            ("es", ["español", "castellano"]),
            ("spa", ["español", "castellano"]),
            ("zh", []),
            ("chi", []),
            ("nosuchlanguage", [])
        ]
    )
    def test_native_names(self, key_name, native_name_value):
        assert LanguageCodes.native_names[key_name] == native_name_value

    @pytest.mark.parametrize("locale,expected", [("en-US", "eng"), ("en", "eng"), ("en-GB", "eng")])
    def test_locale(self, locale, expected):
        assert LanguageCodes.iso_639_2_for_locale(locale) == expected
        assert LanguageCodes.iso_639_2_for_locale("nosuchlocale") is None

    @pytest.mark.parametrize(
        "input_string,expected",
        [
            ("en", "eng"),
            ("eng", "eng"),
            ("en-GB", "eng"),
            ("English", "eng"),
            ("ENGLISH", "eng"),
            ("Nilo-Saharan languages", "ssa"),
        ]
    )
    def test_string_to_alpha_3(self, input_string, expected):
        assert LanguageCodes.string_to_alpha_3(input_string) == expected
        assert LanguageCodes.string_to_alpha_3("NO SUCH LANGUGE") is None

    @pytest.mark.parametrize(
        "input,expected", [
            (["en"], "English"),
            (["eng"], "English"),
            (["es"], "español"),
            (["eng", "spa"], "English/español"),
            ("spa,eng", "español/English"),
            (["spa", "eng", "chi"], "español/English/Chinese"),
            ([], "")
        ]
    )
    def test_name_for_languageset(self, input, expected):
        assert LanguageCodes.name_for_languageset(input) == expected

        with pytest.raises(ValueError):
            LanguageCodes.name_for_languageset(["eng, nxx"])
