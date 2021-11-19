import os
import random
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm.session import Session

from library_registry.app import create_app, test_db_url
from library_registry.model import (
    Admin,
    Audience,
    Base,
    ConfigurationSetting,
    ExternalIntegration,
    Hyperlink,
    Library,
    Place,
    PlaceAlias,
    Resource,
    ServiceArea,
    Validation,
)
from library_registry.model_helpers import get_one_or_create
from library_registry.util import GeometryUtility

TEST_DATA_DIR = Path(os.path.dirname(__file__)) / "data"


def pytest_configure(config):
    """Add configuration options to Pytest"""
    # Register custom markers
    config.addinivalue_line(
        "markers", "needsdocstring: Missing or incomplete GIVEN/WHEN/THEN docstring"
    )
    config.addinivalue_line(
        "markers", "needsdecomposition: Test is too complex, needs to be decomposed"
    )


@pytest.fixture(autouse=True, scope="session")
def init_test_db():
    """For a given testing session, pave and re-initialize the database"""
    engine = create_engine(test_db_url)

    for table in reversed(Base.metadata.sorted_tables):
        try:
            engine.execute(table.delete())
        except ProgrammingError:
            ...

    with engine.connect() as conn:
        Base.metadata.create_all(conn)

    engine.dispose()


@pytest.fixture(scope="session")
def app():
    app = create_app(testing=True)
    app.secret_key = "SUPER SECRET TESTING SECRET"
    yield app


@pytest.fixture
def client(app):
    with app.test_client() as client:
        yield client


@pytest.fixture
def db_engine(app):
    engine = create_engine(test_db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    with db_engine.connect() as connection:
        transaction = connection.begin_nested()
        session = Session(connection)
        yield session
        transaction.rollback()
        session.close()


@pytest.fixture
def admin_user_credentials():
    return ('testadmin', 'testadmin')


@pytest.fixture
def admin_user(db_session, admin_user_credentials):
    (u, p) = admin_user_credentials
    (admin, _) = get_one_or_create(db_session, Admin, username=u)
    admin.password = Admin.make_password(p)
    db_session.commit()
    yield admin
    db_session.delete(admin)
    db_session.commit()


@pytest.fixture
def create_test_library():
    """
    Returns a constructor function for creating a Library object.

    The calling function should clean up created Library objects.

    Example:

        def test_something(db_session, create_test_library):
            my_lib = create_test_library(db_session)

            [...test body...]

            db_session.delete(my_lib)
            db_session.commit()

    """
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
                get_one_or_create(db_session, ServiceArea, library=library, place=place, type=ServiceArea.ELIGIBILITY)

        if focus_areas and isinstance(focus_areas, list):
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

        db_session.commit()

        return library

    return _create_test_library


@pytest.fixture
def create_test_place():
    """
    Returns a constructor function for creating a Place object.

    The calling function should clean up created Place objects.

    Example:

        def test_something(db_session, create_test_place):
            my_place = create_test_place(db_session)

            [...test body...]

            db_session.delete(my_place)
            db_session.commit()

    """
    def _create_test_place(db_session, external_id=None, external_name=None, place_type=None,
                           abbreviated_name=None, parent=None, geometry=None):
        if not geometry:
            latitude = -90 + (random.randrange(1, 800) / 10)
            longitude = -90 + (random.randrange(1, 800) / 10)
            geometry = f"SRID=4326;POINT({latitude} {longitude})"
        elif isinstance(geometry, str):
            if geometry[0] == '{':          # It's probably JSON.
                geometry = GeometryUtility.from_geojson(geometry)
            elif geometry[:5] == 'SRID=':   # It's already a geometry string
                ...                         # so don't do anything.

        external_id = external_id or str(uuid.uuid4())
        external_name = external_name or external_id
        place_type = place_type or Place.CITY
        create_kwargs = {
            "external_id": external_id,
            "external_name": external_name,
            "type": place_type,
            "abbreviated_name": abbreviated_name,
            "parent": parent,
            "geometry": geometry,
        }
        (place, _) = get_one_or_create(db_session, Place, **create_kwargs)
        db_session.commit()
        return place

    return _create_test_place


@pytest.fixture
def create_test_external_integration():
    """
    Returns a constructor function for creating an ExternalIntegration object.

    The calling function should clean up created ExternalIntegration objects.

    Example:

        def test_something(db_session, create_test_external_integration):
            my_integration = create_test_external_integration(db_session)

            [...test body...]

            db_session.delete(my_integration)
            db_session.commit()

    """
    def _create_test_external_integration(db_session, name=None, protocol=None, goal=None, settings=None,
                                          libraries=None, **kwargs):
        integration = None
        create_kwargs = {"name": name or str(uuid.uuid4()), "protocol": protocol or "test_protocol", "goal": goal}

        if not libraries:
            (integration, _) = get_one_or_create(db_session, ExternalIntegration, **create_kwargs)
        else:
            # Make sure we're working with an iterable even if a single library was passed in
            libraries = list(libraries)

            # Try to find an existing integration for one of the given libraries
            for library in libraries:
                integration = ExternalIntegration.lookup(db_session, protocol, goal, library=library)
                if integration:
                    break

            # If that didn't work, create a new one
            if not integration:
                integration = ExternalIntegration(**create_kwargs)
                integration.libraries.extend(libraries)
                db_session.add(integration)

        # At this point we've got an integration from somewhere, lets amend it as necessary
        for attr, value in kwargs.items():
            setattr(integration, attr, value)

        settings = settings or {}
        for k, v in settings.items():
            integration.set_setting(k, v)

        db_session.commit()
        return integration

    return _create_test_external_integration


@pytest.fixture
def create_test_configuration_setting():
    """
    Returns a constructor function for creating a ConfigurationSetting object.

    The calling function should clean up created ConfigurationSetting objects.

    Example:

        def test_something(db_session, create_test_configuration_setting):
            my_setting = create_test_configuration_setting(db_session)

            [...test body...]

            db_session.delete(my_setting)
            db_session.commit()

    """
    def _create_test_configuration_setting(
        db_session, key=None, value=None, library=None, external_integration=None, **kwargs
    ):
        create_kwargs = {"key": key or str(uuid.uuid4())}

        if isinstance(library, Library):
            create_kwargs['library'] = library

        if isinstance(external_integration, ExternalIntegration):
            create_kwargs['external_integration'] = external_integration

        if value:
            create_kwargs['value'] = str(value)

        (setting, _) = get_one_or_create(db_session, ConfigurationSetting, **create_kwargs)

        db_session.commit()
        return setting

    return _create_test_configuration_setting


@pytest.fixture
def create_test_validation():
    """
    Returns a constructor function for creating a Validation object.
    """
    def _create_test_validation(db_session, resource, success=None, started_at=None, secret=None):
        create_kwargs = {"resource": resource}

        for kwarg in ['success', 'started_at', 'secret']:
            if locals()[kwarg] is not None:
                create_kwargs[kwarg] = locals()[kwarg]

        (validation, _) = get_one_or_create(db_session, Validation, **create_kwargs)

        return validation

    return _create_test_validation


@pytest.fixture
def create_test_resource():
    """
    Returns a constructor function for creating a Resource object.
    """
    def _create_test_resource(db_session, href=None, hyperlinks=None, validation=None):
        test_uuid = str(uuid.uuid4())
        create_kwargs = {"href": href or f"http://librarysimplified.org/testresource/{test_uuid}"}

        if hyperlinks and all([isinstance(x, Hyperlink) for x in hyperlinks]):
            create_kwargs["hyperlinks"] = hyperlinks

        if validation and isinstance(validation, Validation):
            create_kwargs["validation"] = validation

        (resource, _) = get_one_or_create(db_session, Resource, **create_kwargs)

        return resource

    return _create_test_resource


##############################################################################
# Places
##############################################################################

@pytest.fixture
def crude_us(db_session, create_test_place):
    """
    A Place representing the United States. Unlike other Places in this series, this is
    backed by a crude GeoJSON drawing of the continental United States, not the much more
    complex GeoJSON that would be obtained from an official source. This shape includes
    large chunks of ocean, as well as portions of Canada and Mexico.
    """
    place = create_test_place(
        db_session, external_id="US", external_name="United States", place_type=Place.NATION,
        abbreviated_name="US", parent=None, geometry=(TEST_DATA_DIR / 'crude_us_geojson.json').read_text()
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def new_york_state(db_session, create_test_place, crude_us):
    place = create_test_place(
        db_session, external_id="36", external_name="New York", place_type=Place.STATE,
        abbreviated_name="NY", parent=crude_us,
        geometry=(TEST_DATA_DIR / 'ny_state_geojson.json').read_text()
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def connecticut_state(db_session, create_test_place, crude_us):
    place = create_test_place(
        db_session, external_id="09", external_name="Connecticut", place_type=Place.STATE,
        abbreviated_name="CT", parent=crude_us,
        geometry=(TEST_DATA_DIR / 'ct_state_geojson.json').read_text()
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def kansas_state(db_session, create_test_place, crude_us):
    place = create_test_place(
        db_session, external_id="20", external_name="Kansas", place_type=Place.STATE,
        abbreviated_name="KS", parent=crude_us,
        geometry=(TEST_DATA_DIR / 'kansas_state_geojson.json').read_text()
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def massachusetts_state(db_session, create_test_place, crude_us):
    place = create_test_place(
        db_session, external_id="25", external_name="Massachusetts", place_type=Place.STATE,
        abbreviated_name="MA", parent=crude_us, geometry=None
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def new_mexico_state(db_session, create_test_place, crude_us):
    place = create_test_place(
        db_session, external_id="NM", external_name="New Mexico", place_type=Place.STATE,
        abbreviated_name="NM", parent=crude_us,
        geometry=(TEST_DATA_DIR / 'new_mexico_state_geojson.json').read_text()
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def new_york_city(db_session, create_test_place, new_york_state):
    place = create_test_place(
        db_session, external_id="365100", external_name="New York", place_type=Place.CITY,
        abbreviated_name=None, parent=new_york_state,
        geometry=(TEST_DATA_DIR / 'ny_city_geojson.json').read_text()
    )
    for place_alias in ["Manhattan", "Brooklyn", "New York"]:
        get_one_or_create(db_session, PlaceAlias, place=place, name=place_alias)

    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def crude_kings_county(db_session, create_test_place, new_york_state):
    """
    A Place representing Kings County, NY. Unlike other Places in this series, this is
    backed by a crude GeoJSON drawing of Kings County, not the much more complex GeoJSON
    that would be obtained from an official source.
    """
    place = create_test_place(
        db_session, external_id="Kings", external_name="Kings", place_type=Place.COUNTY,
        abbreviated_name=None, parent=new_york_state,
        geometry=(TEST_DATA_DIR / 'crude_kings_county_geojson.json').read_text()
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def lake_placid_ny(db_session, create_test_place, new_york_state):
    place = create_test_place(
        db_session, external_id="LakePlacid", external_name="Lake Placid", place_type=Place.CITY,
        abbreviated_name=None, parent=new_york_state,
        geometry='SRID=4326;POINT(-73.59 44.17)'
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def crude_new_york_county(db_session, create_test_place, new_york_state):
    place = create_test_place(
        db_session, external_id="Manhattan", external_name="New York County", place_type=Place.COUNTY,
        abbreviated_name="NY", parent=new_york_state,
        geometry=(TEST_DATA_DIR / 'crude_new_york_county_geojson.json').read_text()
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def zip_10018(db_session, create_test_place, new_york_state):
    """ZIP code 10018, in the east side of midtown Manhattan, NYC"""
    place = create_test_place(
        db_session, external_id="10018", external_name="10018", place_type=Place.POSTAL_CODE,
        abbreviated_name=None, parent=new_york_state,
        geometry=(TEST_DATA_DIR / 'zip_10018_geojson.json').read_text()
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def zip_11212(db_session, create_test_place, new_york_state):
    """ZIP code 11212, in Brooklyn, NYC"""
    place = create_test_place(
        db_session, external_id="11212", external_name="11212", place_type=Place.POSTAL_CODE,
        abbreviated_name=None, parent=new_york_state,
        geometry=(TEST_DATA_DIR / 'zip_11212_geojson.json').read_text()
    )
    get_one_or_create(db_session, PlaceAlias, place=place, name="Brooklyn")
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def zip_12601(db_session, create_test_place, new_york_state):
    """ZIP code 12601, in Poughkeepsie, NY"""
    place = create_test_place(
        db_session, external_id="12601", external_name="12601", place_type=Place.POSTAL_CODE,
        abbreviated_name=None, parent=new_york_state,
        geometry=(TEST_DATA_DIR / 'zip_12601_geojson.json').read_text()
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def crude_albany(db_session, create_test_place, new_york_state):
    """Crude representation of Albany, NY"""
    place = create_test_place(
        db_session, external_id="Albany", external_name="Albany", place_type=Place.CITY,
        abbreviated_name=None, parent=new_york_state,
        geometry=(TEST_DATA_DIR / 'crude_albany_geojson.json').read_text()
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def boston_ma(db_session, create_test_place, massachusetts_state):
    """Boston, Massachusetts"""
    place = create_test_place(
        db_session, external_id="2507000", external_name="Boston", place_type=Place.CITY,
        abbreviated_name=None, parent=massachusetts_state,
        geometry=(TEST_DATA_DIR / 'boston_geojson.json').read_text()
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def manhattan_ks(db_session, create_test_place, kansas_state):
    """Manhattan, Kansas"""
    place = create_test_place(
        db_session, external_id="2044250", external_name="Manhattan", place_type=Place.CITY,
        abbreviated_name=None, parent=kansas_state,
        geometry=(TEST_DATA_DIR / 'manhattan_ks_geojson.json').read_text()
    )
    db_session.commit()
    yield place
    db_session.delete(place)
    db_session.commit()


@pytest.fixture
def places(
    crude_us,
    new_york_state,
    connecticut_state,
    kansas_state,
    massachusetts_state,
    new_mexico_state,
    new_york_city,
    crude_kings_county,
    lake_placid_ny,
    crude_new_york_county,
    zip_10018,
    zip_11212,
    zip_12601,
    crude_albany,
    boston_ma,
    manhattan_ks
):
    """All the Place fixtures as a dictionary"""
    return {
        "crude_us": crude_us,
        "new_york_state": new_york_state,
        "connecticut_state": connecticut_state,
        "kansas_state": kansas_state,
        "massachusetts_state": massachusetts_state,
        "new_mexico_state": new_mexico_state,
        "new_york_city": new_york_city,
        "crude_kings_county": crude_kings_county,
        "lake_placid_ny": lake_placid_ny,
        "crude_new_york_county": crude_new_york_county,
        "zip_10018": zip_10018,
        "zip_11212": zip_11212,
        "zip_12601": zip_12601,
        "crude_albany": crude_albany,
        "boston_ma": boston_ma,
        "manhattan_ks": manhattan_ks,
    }


##############################################################################
# Libraries
##############################################################################


@pytest.fixture
def nypl(db_session, create_test_library, new_york_city, zip_11212):
    """The New York Public Library"""
    library = create_test_library(
        db_session, library_name="NYPL", short_name="nypl",
        eligibility_areas=[new_york_city, zip_11212], has_email=True
    )
    db_session.commit()
    yield library

@pytest.fixture
def connecticut_state_library(db_session, create_test_library, connecticut_state):
    """The Connecticut State Library"""
    library = create_test_library(
        db_session, library_name="Connecticut State Library", short_name="CT",
        eligibility_areas=[connecticut_state], has_email=True
    )
    db_session.commit()
    yield library


@pytest.fixture
def kansas_state_library(db_session, create_test_library, kansas_state, manhattan_ks):
    """The Kansas State Library"""
    library = create_test_library(
        db_session, library_name="Kansas State Library", short_name="KS",
        eligibility_areas=[kansas_state, manhattan_ks], has_email=True
    )
    db_session.commit()
    yield library


@pytest.fixture
def libraries(connecticut_state_library, kansas_state_library, nypl):
    """All the Library fixtures as a dictionary"""
    return {
        "kansas_state_library": kansas_state_library,
        "nypl": nypl,
        "connecticut_state_library": connecticut_state_library,
    }
