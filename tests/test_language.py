import pytest

from util.language import LanguageCodes


class TestLanguageCodes:
    def test_lookups(self):
        c = LanguageCodes
        assert c.two_to_three["en"] == "eng"
        assert c.three_to_two["eng"] == "en"
        assert c.english_names["en"] == ["English"]
        assert c.english_names["eng"] == ["English"]
        assert c.native_names["en"] == ["English"]
        assert c.native_names["eng"] == ["English"]

        assert c.two_to_three["es"] == "spa"
        assert c.three_to_two["spa"] == "es"
        assert c.english_names["es"] == ["Spanish", "Castilian"]
        assert c.english_names["spa"] == ["Spanish", "Castilian"]
        assert c.native_names["es"] == ["español", "castellano"]
        assert c.native_names["spa"] == ["español", "castellano"]

        assert c.two_to_three["zh"] == "chi"
        assert c.three_to_two["chi"] == "zh"
        assert c.english_names["zh"] == ["Chinese"]
        assert c.english_names["chi"] == ["Chinese"]
        # We don't have this translation yet.
        assert c.native_names["zh"] == []
        assert c.native_names["chi"] == []

        assert c.two_to_three["nosuchlanguage"] is None
        assert c.three_to_two["nosuchlanguage"] is None
        assert c.english_names["nosuchlanguage"] == []
        assert c.native_names["nosuchlanguage"] == []

    def test_locale(self):
        m = LanguageCodes.iso_639_2_for_locale
        assert m("en-US") == "eng"
        assert m("en") == "eng"
        assert m("en-GB") == "eng"
        assert m("nq-none") is None

    def test_string_to_alpha_3(self):
        m = LanguageCodes.string_to_alpha_3
        assert m("en") == "eng"
        assert m("eng") == "eng"
        assert m("en-GB") == "eng"
        assert m("English") == "eng"
        assert m("ENGLISH") == "eng"
        assert m("Nilo-Saharan languages") == "ssa"
        assert m("NO SUCH LANGUAGE") is None

    def test_name_for_languageset(self):
        m = LanguageCodes.name_for_languageset
        assert m([]) == ""
        assert m(["en"]) == "English"
        assert m(["eng"]) == "English"
        assert m(["es"]) == "español"
        assert m(["eng", "spa"]) == "English/español"
        assert m("spa,eng") == "español/English"
        assert m(["spa", "eng", "chi"]) == "español/English/Chinese"
        with pytest.raises(ValueError):
            m(["eng, nxx"])
