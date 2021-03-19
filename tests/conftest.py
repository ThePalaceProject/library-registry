import os
from pathlib import Path
import random
import uuid

import pytest

from library_registry.geometry_loader import GeometryUtility
from library_registry.config import Configuration
from library_registry.model import (
    get_one_or_create,
    Base,
    Place,
    PlaceAlias,
    SessionManager,
    Library,
    ServiceArea,
    Audience,
    Hyperlink,
    ExternalIntegration,
)

##############################################################################
# Function Level Fixtures                                                    #
##############################################################################


@pytest.fixture
def flask_client():
    ...


@pytest.fixture(scope="function")
def db_session():
    """Supply a new session object for the test database to every test function"""
    session = SessionManager.session(Configuration.database_url(test=True))
    yield session
    session.close()


@pytest.fixture(scope="function")
def create_external_integration():
    """Factory that supplies a function for creating test external integrations"""
    def _create_external_integration(db_session, protocol, goal=None, settings=None, libraries=None, **kwargs):
        integration = None
        if not libraries:
            (integration, _) = get_one_or_create(db_session, ExternalIntegration, protocol=protocol, goal=goal)
        else:
            if not isinstance(libraries, list) and not isinstance(libraries, tuple):
                libraries = [libraries]

            # Attempt to find existing integration for the given libraries
            for library in libraries:
                integration = ExternalIntegration.lookup(db_session, protocol, goal, library=library)
                if integration:
                    break

            # If we don't have one by now, create a new one
            if not integration:
                integration = ExternalIntegration(protocol=protocol, goal=goal)
                integration.libraries.extend(libraries)
                db_session.add(integration)

        for attr, value in kwargs.items():
            setattr(integration, attr, value)

        settings = settings or {}
        for k, v in settings.items():
            integration.set_setting(k, v)

        return integration

    return _create_external_integration


##############################################################################
# Session Level Fixtures                                                     #
##############################################################################


@pytest.fixture(autouse=True, scope="session")
def init_test_db():
    """For a given testing session, pave and re-initialize the database"""
    (engine, conn) = SessionManager.initialize(Configuration.database_url(test=True))

    for table in reversed(Base.metadata.sorted_tables):
        engine.execute(table.delete())

    Base.metadata.create_all(conn)

    conn.close()
    engine.dispose()


@pytest.fixture(scope="session")
def persistent_db_session():
    session = SessionManager.session(Configuration.database_url(test=True))
    yield session
    session.close()


@pytest.fixture
def vendor_id_node_value():
    return "0x685b35c00f05"


@pytest.fixture
def vendor_id(vendor_id_node_value):
    ...


@pytest.fixture(scope="session")
def create_test_place():
    """Returns a factory function for creating places for tests"""
    def _create_test_place(db_session, external_id=None, external_name=None, place_type=None,
                           abbreviated_name=None, parent=None, geometry=None):
        if not geometry:
            latitude = -90 + (random.randrange(1, 800) / 10)
            longitude = -90 + (random.randrange(1, 800) / 10)
            geometry = f"SRID=4326;POINT({latitude} {longitude})"
        elif isinstance(geometry, str):
            geometry = GeometryUtility.from_geojson(geometry)

        external_id = external_id or str(uuid.uuid4())
        external_name = external_name or external_id
        place_type = place_type or Place.CITY
        create_kwargs = {
            "external_id": external_id,
            "external_name": external_name,
            "type": place_type,
            "abbreviated_name": abbreviated_name,
            "parent": parent,
        }
        (place, _) = get_one_or_create(db_session, Place, **create_kwargs)
        db_session.commit()
        return place

    return _create_test_place


@pytest.fixture(scope="session")
def places(persistent_db_session, create_test_place):
    TEST_DATA_DIR = Path(os.path.dirname(__file__)) / "data"
    """Returns a dict of test Places"""
    test_places = {}

    # A Place representing the United States. Unlike other Places in this series, this is
    # backed by a crude GeoJSON drawing of the continental United States, not the much more
    # complex GeoJSON that would be obtained from an official source. This shape includes
    # large chunks of ocean, as well as portions of Canada and Mexico.
    test_places["crude_us"] = create_test_place(
        persistent_db_session, external_id="US", external_name="United States", place_type=Place.NATION,
        abbreviated_name="US", parent=None, geometry=(TEST_DATA_DIR / 'crude_us_geojson.json').read_text()
    )

    #####################################
    # States                            #
    #####################################

    # New York
    test_places["new_york_state"] = create_test_place(
        persistent_db_session, external_id="36", external_name="New York", place_type=Place.STATE,
        abbreviated_name="NY", parent=test_places["crude_us"],
        geometry=(TEST_DATA_DIR / 'ny_state_geojson.json').read_text()
    )

    # Connecticut
    test_places["connecticut_state"] = create_test_place(
        persistent_db_session, external_id="09", external_name="Connecticut", place_type=Place.STATE,
        abbreviated_name="CT", parent=test_places["crude_us"],
        geometry=(TEST_DATA_DIR / 'ct_state_geojson.json').read_text()
    )

    # Kansas
    test_places["kansas_state"] = create_test_place(
        persistent_db_session, external_id="20", external_name="Kansas", place_type=Place.STATE,
        abbreviated_name="KS", parent=test_places["crude_us"],
        geometry=(TEST_DATA_DIR / 'kansas_state_geojson.json').read_text()
    )

    # Massachusetts
    test_places["massachusetts_state"] = create_test_place(
        persistent_db_session, external_id="25", external_name="Massachusetts", place_type=Place.STATE,
        abbreviated_name="MA", parent=test_places["crude_us"], geometry=None
    )

    # New Mexico
    test_places["new_mexico_state"] = create_test_place(
        persistent_db_session, external_id="NM", external_name="New Mexico", place_type=Place.STATE,
        abbreviated_name="NM", parent=test_places["crude_us"],
        geometry=(TEST_DATA_DIR / 'new_mexico_state_geojson.json').read_text()
    )

    #####################################
    # Places in New York State          #
    #####################################

    # New York City
    test_places["new_york_city"] = create_test_place(
        persistent_db_session, external_id="365100", external_name="New York", place_type=Place.CITY,
        abbreviated_name=None, parent=test_places["new_york_state"],
        geometry=(TEST_DATA_DIR / 'ny_city_geojson.json').read_text()
    )
    # PlaceAliases for the New York City Place
    get_one_or_create(persistent_db_session, PlaceAlias, place=test_places["new_york_city"], name="Manhattan")
    get_one_or_create(persistent_db_session, PlaceAlias, place=test_places["new_york_city"], name="Brooklyn")
    get_one_or_create(persistent_db_session, PlaceAlias, place=test_places["new_york_city"], name="New York")

    # A Place representing Kings County, NY. Unlike other Places in this series, this is
    # backed by a crude GeoJSON drawing of Kings County, not the much more complex GeoJSON
    # that would be obtained from an official source.
    test_places["crude_kings_county"] = create_test_place(
        persistent_db_session, external_id="Kings", external_name="Kings", place_type=Place.COUNTY,
        abbreviated_name=None, parent=test_places["new_york_state"],
        geometry=(TEST_DATA_DIR / 'crude_kings_county_geojson.json').read_text()
    )

    # Crude New York County
    test_places["crude_new_york_county"] = create_test_place(
        persistent_db_session, external_id="Manhattan", external_name="New York County", place_type=Place.COUNTY,
        abbreviated_name="NY", parent=test_places["new_york_state"],
        geometry=(TEST_DATA_DIR / 'crude_new_york_county_geojson.json').read_text()
    )

    # ZIP code 10018, in the east side of midtown Manhattan, NYC
    test_places["zip_10018"] = create_test_place(
        persistent_db_session, external_id="10018", external_name="10018", place_type=Place.POSTAL_CODE,
        abbreviated_name=None, parent=test_places["new_york_state"],
        geometry=(TEST_DATA_DIR / 'zip_10018_geojson.json').read_text()
    )

    # ZIP code 11212, in Brooklyn, NYC
    test_places["zip_11212"] = create_test_place(
        persistent_db_session, external_id="11212", external_name="11212", place_type=Place.POSTAL_CODE,
        abbreviated_name=None, parent=test_places["new_york_state"],
        geometry=(TEST_DATA_DIR / 'zip_11212_geojson.json').read_text()
    )
    # PlaceAlias for the zip_11212 Place
    get_one_or_create(persistent_db_session, PlaceAlias, place=test_places["zip_11212"], name="Brooklyn")

    # ZIP code 12601, in Poughkeepsie, NY
    test_places["zip_12601"] = create_test_place(
        persistent_db_session, external_id="12601", external_name="12601", place_type=Place.POSTAL_CODE,
        abbreviated_name=None, parent=test_places["new_york_state"],
        geometry=(TEST_DATA_DIR / 'zip_12601_geojson.json').read_text()
    )

    # Crude representation of Albanay, NY
    test_places["crude_albany"] = create_test_place(
        persistent_db_session, external_id="Albany", external_name="Albany", place_type=Place.CITY,
        abbreviated_name=None, parent=test_places["new_york_state"],
        geometry=(TEST_DATA_DIR / 'crude_albany_geojson.json').read_text()
    )

    #####################################
    # Places in Massachusetts           #
    #####################################

    # Boston, MA
    test_places["boston_ma"] = create_test_place(
        persistent_db_session, external_id="2507000", external_name="Boston", place_type=Place.CITY,
        abbreviated_name=None, parent=test_places["massachusetts_state"],
        geometry=(TEST_DATA_DIR / 'boston_geojson.json').read_text()
    )

    #####################################
    # Places in Kansas                  #
    #####################################

    # Manhattan, KS
    test_places["manhattan_ks"] = create_test_place(
        persistent_db_session, external_id="2044250", external_name="Manhattan", place_type=Place.CITY,
        abbreviated_name=None, parent=test_places["kansas_state"],
        geometry=(TEST_DATA_DIR / 'manhattan_ks_geojson.json').read_text()
    )

    return test_places


@pytest.fixture(scope="session")
def create_test_library():
    def _create_test_library(db_session, library_name=None, short_name=None, eligibility_areas=None,
                             focus_areas=None, audiences=None, library_stage=Library.PRODUCTION_STAGE,
                             registry_stage=Library.PRODUCTION_STAGE, has_email=False, description=None):
        library_name = library_name or str(uuid.uuid4())
        create_kwargs = {
            "authentication_url": f"https://{library_name}/",
            "opds_url": f"https://{library_name}/",
            "short_name": short_name or library_name,
            "shared_secret": library_name,
            "description": description or library_name,
            "library_stage": library_stage,
            "registry_stage": registry_stage,
        }
        (library, _) = get_one_or_create(db_session, Library, name=library_name, create_method_kwargs=create_kwargs)

        if eligibility_areas and isinstance(eligibility_areas, list):
            for place in eligibility_areas:
                if not isinstance(place, Place):
                    # TODO: Emit a warning
                    continue
                get_one_or_create(db_session, ServiceArea, library=library, place=place, type=ServiceArea.FOCUS)

        if focus_areas and isinstance(eligibility_areas, list):
            for place in focus_areas:
                if not isinstance(place, Place):
                    # TODO: Emit a warning
                    continue
                get_one_or_create(db_session, ServiceArea, library=library, place=place, type=ServiceArea.FOCUS)

        audiences = audiences or [Audience.PUBLIC]
        library.audiences = [Audience.lookup(db_session, audience) for audience in audiences]

        if has_email:
            library.set_hyperlink(Hyperlink.INTEGRATION_CONTACT_REL, f"mailto:{library_name}@library.org")
            library.set_hyperlink(Hyperlink.HELP_REL, f"mailto:{library_name}@library.org")
            library.set_hyperlink(Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL, f"mailto:{library_name}@library.org")

        return library

    return _create_test_library


@pytest.fixture(scope="session")
def libraries(persistent_db_session, create_test_library, places):
    """Returns a dict of test Libraries"""
    test_libraries = {}

    # New York Public Library
    test_libraries["nypl"] = create_test_library(
        persistent_db_session, library_name="NYPL", short_name="nypl",
        eligibility_areas=[places["new_york_city"], places["zip_11212"]], has_email=True
    )

    # Connecticut State Library
    test_libraries["connecticut_state_library"] = create_test_library(
        persistent_db_session, library_name="Connecticut State Library", short_name="CT",
        eligibility_areas=[places["connecticut_state"]], has_email=True
    )

    test_libraries["kansas_state_library"] = create_test_library(
        persistent_db_session, library_name="Kansas State Library", short_name="KS",
        eligibility_areas=[places["kansas_state"]], has_email=True
    )

    return test_libraries
