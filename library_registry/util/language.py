import os
from collections import defaultdict
from pkg_resources import resource_string


class LanguageCodes():
    """Convert between ISO-639-2 and ISO-693-1 language codes.

    The data file comes from
    http://www.loc.gov/standards/iso639-2/ISO-639-2_utf-8.txt
    """

    two_to_three = defaultdict(lambda: None)
    three_to_two = defaultdict(lambda: None)
    english_names = defaultdict(list)
    english_names_to_three = defaultdict(lambda: None)
    native_names = defaultdict(list)

    RAW_DATA = resource_string('library_registry', 'data/ISO-639-2_utf-8.txt').decode('utf-8')

    NATIVE_NAMES_RAW_DATA = [
        {"code": "en", "name": "English", "nativeName": "English"},
        {"code": "fr", "name": "French", "nativeName": "français"},
        {"code": "de", "name": "German", "nativeName": "Deutsch"},
        {"code": "el", "name": "Greek, Modern", "nativeName": "Ελληνικά"},
        {"code": "hu", "name": "Hungarian", "nativeName": "Magyar"},
        {"code": "it", "name": "Italian", "nativeName": "Italiano"},
        {"code": "no", "name": "Norwegian", "nativeName": "Norsk"},
        {"code": "pl", "name": "Polish", "nativeName": "polski"},
        {"code": "pt", "name": "Portuguese", "nativeName": "Português"},
        {"code": "ru", "name": "Russian", "nativeName": "русский"},
        {"code": "es", "name": "Spanish, Castilian", "nativeName": "español, castellano"},
        {"code": "sv", "name": "Swedish", "nativeName": "svenska"},
    ]

    for i in RAW_DATA.split("\n"):
        (alpha_3, terminologic_code, alpha_2, names,
         french_names) = i.strip().split("|")
        names = [x.strip() for x in names.split(";")]
        if alpha_2:
            three_to_two[alpha_3] = alpha_2
            english_names[alpha_2] = names
            two_to_three[alpha_2] = alpha_3
        for name in names:
            english_names_to_three[name.lower()] = alpha_3
        english_names[alpha_3] = names

    for i in NATIVE_NAMES_RAW_DATA:
        alpha_2 = i['code']
        alpha_3 = two_to_three[alpha_2]
        names = i['nativeName']
        names = [x.strip() for x in names.split(",")]
        native_names[alpha_2] = names
        native_names[alpha_3] = names

    def languages_from_accept(accept_languages):
        """Turn a list of (locale, quality) 2-tuples into a list of language codes."""
        seen = set([])
        languages = []
        for locale, quality in accept_languages:
            language = LanguageCodes.iso_639_2_for_locale(locale)
            if language and language not in seen:
                languages.append(language)
                seen.add(language)
        if not languages:
            languages = os.environ.get('DEFAULT_LANGUAGES', 'eng')
            languages = languages.split(',')
        return languages

    @classmethod
    def iso_639_2_for_locale(cls, locale):
        """Turn a locale code into an ISO-639-2 alpha-3 language code."""
        if '-' in locale:
            language, place = locale.lower().split("-", 1)
        else:
            language = locale
        if cls.two_to_three[language]:
            return cls.two_to_three[language]
        elif cls.three_to_two[language]:            # It's already ISO-639-2.
            return language
        return None

    @classmethod
    def string_to_alpha_3(cls, s):
        """Try really hard to convert a string to an ISO-639-2 alpha-3 language code."""
        if not s:
            return None
        s = s.lower()
        if s in cls.english_names_to_three:  # It's the English name of a language.
            return cls.english_names_to_three[s]

        if "-" in s:
            s = s.split("-")[0]

        if s in cls.three_to_two:       # It's already an alpha-3.
            return s
        elif s in cls.two_to_three:     # It's an alpha-2.
            return cls.two_to_three[s]

        return None

    @classmethod
    def name_for_languageset(cls, languages):
        if isinstance(languages, str):
            languages = languages.split(",")
        all_names = []
        if not languages:
            return ""
        for lang in languages:
            normalized = cls.string_to_alpha_3(lang)
            native_names = cls.native_names.get(normalized, [])
            if native_names:
                all_names.append(native_names[0])
            else:
                names = cls.english_names.get(normalized, [])
                if not names:
                    raise ValueError("No native or English name for %s" % lang)
                all_names.append(names[0])
        if len(all_names) == 1:
            return all_names[0]
        return "/".join(all_names)
