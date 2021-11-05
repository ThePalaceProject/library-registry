##############################################################################
# Sitewide Configuration Value Names
##############################################################################

ADOBE_VENDOR_ID = "vendor_id"

ADOBE_VENDOR_ID_NODE_VALUE = "node_value"

ADOBE_VENDOR_ID_DELEGATE_URL = "delegate_url"

BASE_URL = "base_url"

# Default nation for any place not explicitly in a particular nation.
DEFAULT_NATION_ABBREVIATION = "default_nation_abbreviation"

# For performance reasons, a registry may want to omit certain pieces of information from
# large feeds. This sitewide setting controls how big a feed must be to be considered 'large'.
LARGE_FEED_SIZE = "large_feed_size"

# URL of the terms of service document for library registration
REGISTRATION_TERMS_OF_SERVICE_URL = "registration_terms_of_service_url"

# HTML snippet describing the ToS for library registration. It's better if this
# is a short snippet of text with a link rather than the actual text of the ToS.
REGISTRATION_TERMS_OF_SERVICE_HTML = "registration_terms_of_service_html"

# Email address used for:
#   - From: address of transactional mail sent by the Library Registry
#   - contact address for people having problems with the registry
REGISTRY_CONTACT_EMAIL = "registry_contact_email"

# URL of a web based client to the registry. Must be templated and contain
# a `{uuid}` expression to provide the web URL for a specific library.
WEB_CLIENT_URL = "web_client_url"

##############################################################################
# Media Types
##############################################################################

AUTHENTICATION_DOCUMENT_MEDIA_TYPE = "application/vnd.opds.authentication.v1.0+json"

PROBLEM_DETAIL_JSON_MEDIA_TYPE = "application/api-problem+json"

OPDS_MEDIA_TYPE = "application/opds+json"

OPDS_CATALOG_MEDIA_TYPE = "application/atom+xml;profile=opds-catalog"

OPDS_1_MEDIA_TYPE = f"{OPDS_CATALOG_MEDIA_TYPE};kind=acquisition"

OPDS_CATALOG_REGISTRATION_MEDIA_TYPE = (
    "application/opds+json;profile=https://librarysimplified.org/rel/profile/directory"
)

OPENSEARCH_MEDIA_TYPE = "application/opensearchdescription+xml"

##############################################################################
# Relation URIs
##############################################################################

##############################################################################
# Place Types
##############################################################################
PLACE_NATION                  = 'nation'                  # noqa: E221
PLACE_STATE                   = 'state'                   # noqa: E221
PLACE_COUNTY                  = 'county'                  # noqa: E221
PLACE_CITY                    = 'city'                    # noqa: E221
PLACE_POSTAL_CODE             = 'postal_code'             # noqa: E221
PLACE_LIBRARY_SERVICE_AREA    = 'library_service_area'    # noqa: E221
PLACE_EVERYWHERE              = 'everywhere'              # noqa: E221


##############################################################################
# Constant Classes
##############################################################################
class LibraryType:
    """
    Constant container for library types.

    This is as defined here:

    https://github.com/NYPL-Simplified/Simplified/wiki/LibraryRegistryPublicAPI#the-subject-scheme-for-library-types
    """

    SCHEME_URI  = "http://librarysimplified.org/terms/library-types"        # noqa: E221
    LOCAL       = "local"                                                   # noqa: E221
    COUNTY      = "county"                                                  # noqa: E221
    STATE       = "state"                                                   # noqa: E221
    PROVINCE    = "province"                                                # noqa: E221
    NATIONAL    = "national"                                                # noqa: E221
    UNIVERSAL   = "universal"                                               # noqa: E221

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


##############################################################################
# Search Related
##############################################################################
US_STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AR": "Arkansas",
    "AZ": "Arizona",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DC": "District of Columbia",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "IA": "Iowa",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "MA": "Massachusetts",
    "MD": "Maryland",
    "ME": "Maine",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MO": "Missouri",
    "MS": "Mississippi",
    "MT": "Montana",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "NE": "Nebraska",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NV": "Nevada",
    "NY": "New York",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "PR": "Puerto Rico",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VA": "Virginia",
    "VT": "Vermont",
    "WA": "Washington",
    "WI": "Wisconsin",
    "WV": "West Virginia",
    "WY": "Wyoming",
}

US_STATE_ABBREVIATIONS = [abbreviation.lower() for abbreviation in US_STATES.keys()]

US_STATE_NAMES = [state.lower() for state in US_STATES.values()]

MULTI_WORD_STATE_NAMES = [name for name in US_STATE_NAMES if ' ' in name]

LIBRARY_KEYWORDS = [
    'archive',
    'bookmobile',
    'bookmobiles',
    'college',
    'free',
    'library',
    'memorial',
    'public',
    'regional',
    'research',
    'university',
]
