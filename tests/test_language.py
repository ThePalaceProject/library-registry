import pytest

from library_registry.util.language import LanguageCodes


class TestLanguageCodes:
    def test_lookups(self):
        c = LanguageCodes
        assert c.two_to_three['en'] == "eng"
        assert "en" == c.three_to_two['eng']
        assert ["English"] == c.english_names['en']
        assert ["English"] == c.english_names['eng']
        assert ["English"] == c.native_names['en']
        assert ["English"] == c.native_names['eng']

        assert "spa" == c.two_to_three['es']
        assert "es" == c.three_to_two['spa']
        assert ['Spanish', 'Castilian'] == c.english_names['es']
        assert ['Spanish', 'Castilian'] == c.english_names['spa']
        assert ["español", "castellano"] == c.native_names['es']
        assert ["español", "castellano"] == c.native_names['spa']

        assert "chi" == c.two_to_three['zh']
        assert "zh" == c.three_to_two['chi']
        assert ["Chinese"] == c.english_names['zh']
        assert ["Chinese"] == c.english_names['chi']
        # We don't have this translation yet.
        assert [] == c.native_names['zh']
        assert [] == c.native_names['chi']

        assert c.two_to_three['nosuchlanguage'] is None
        assert c.three_to_two['nosuchlanguage'] is None
        assert [] == c.english_names['nosuchlanguage']
        assert [] == c.native_names['nosuchlanguage']

    def test_locale(self):
        m = LanguageCodes.iso_639_2_for_locale
        assert "eng" == m("en-US")
        assert "eng" == m("en")
        assert "eng" == m("en-GB")
        assert m("nq-none") is None

    def test_string_to_alpha_3(self):
        m = LanguageCodes.string_to_alpha_3
        assert "eng" == m("en")
        assert "eng" == m("eng")
        assert "eng" == m("en-GB")
        assert "eng" == m("English")
        assert "eng" == m("ENGLISH")
        assert "ssa" == m("Nilo-Saharan languages")
        assert m("NO SUCH LANGUAGE") is None

    def test_name_for_languageset(self):
        m = LanguageCodes.name_for_languageset
        assert "" == m([])
        assert "English" == m(["en"])
        assert "English" == m(["eng"])
        assert "español" == m(['es'])
        assert "English/español" == m(["eng", "spa"])
        assert "español/English" == m("spa,eng")
        assert "español/English/Chinese" == m(["spa", "eng", "chi"])

        with pytest.raises(ValueError):
            m(["eng, nxx"])
