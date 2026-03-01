"""Library type constants."""


class LibraryType:
    """Constant container for library types.

    This is as defined here:

    https://github.com/NYPL-Simplified/Simplified/wiki/LibraryRegistryPublicAPI#the-subject-scheme-for-library-types
    """

    SCHEME_URI = "http://librarysimplified.org/terms/library-types"
    LOCAL = "local"
    COUNTY = "county"
    STATE = "state"
    PROVINCE = "province"
    NATIONAL = "national"
    UNIVERSAL = "universal"

    # Different nations use different terms for referring to their
    # administrative divisions, which translates into different terms in
    # the library type vocabulary.
    ADMINISTRATIVE_DIVISION_TYPES = {
        "US": STATE,
        "CA": PROVINCE,
    }

    NAME_FOR_CODE = {
        LOCAL: "Local library",
        COUNTY: "County library",
        STATE: "State library",
        PROVINCE: "Provincial library",
        NATIONAL: "National library",
        UNIVERSAL: "Online library",
    }
