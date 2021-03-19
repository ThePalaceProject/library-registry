import json
import os
import pathlib
from datetime import datetime, timedelta
from io import BytesIO

import pytest
from library_registry.config import Configuration
from library_registry.log import LogConfiguration
from library_registry.model import (Admin, Audience, Base,
                                    ConfigurationSetting, ExternalIntegration,
                                    Hyperlink, Library, Place, PlaceAlias,
                                    ServiceArea, SessionManager,
                                    get_one_or_create)
from library_registry.util import GeometryUtility
from library_registry.util.http import BadResponseException
from sqlalchemy.orm.exc import MultipleResultsFound, NoResultFound
from sqlalchemy.orm.session import Session


def package_setup():
    """Make sure the database schema is initialized and initial
    data is in place.
    """
    engine, connection = DatabaseTest.get_database_connection()

    # First, recreate the schema.
    for table in reversed(Base.metadata.sorted_tables):
        engine.execute(table.delete())

    Base.metadata.create_all(connection)

    # Initialize basic database data needed by the application.
    _db = Session(connection)
    SessionManager.initialize_data(_db)
    _db.commit()

    LogConfiguration.initialize(_db)

    connection.close()
    engine.dispose()


shared_datadir = pathlib.Path(__file__).parent / 'data'


class DatabaseTest():

    engine = None
    connection = None

    # The following class attributes are pulled as strings out of json files in the tests/data
    # directory, via the pytest-datadir plugin. See
    #
    #     https://pypi.org/project/pytest-datadir/
    #
    # for documentation.
    new_york_state_geojson = (shared_datadir / 'ny_state_geojson.json').read_text()
    connecticut_state_geojson = (shared_datadir / 'ct_state_geojson.json').read_text()
    new_york_city_geojson = (shared_datadir / 'ny_city_geojson.json').read_text()
    zip_10018_geojson = (shared_datadir / 'zip_10018_geojson.json').read_text()
    zip_12601_geojson = (shared_datadir / 'zip_12601_geojson.json').read_text()
    crude_kings_county_geojson = (shared_datadir / 'crude_kings_county_geojson.json').read_text()
    connecticut_geojson = (shared_datadir / 'connecticut_geojson.json').read_text()
    kansas_state_geojson = (shared_datadir / 'kansas_state_geojson.json').read_text()
    manhattan_ks_geojson = (shared_datadir / 'manhattan_ks_geojson.json').read_text()
    boston_geojson = (shared_datadir / 'boston_geojson.json').read_text()
    zip_11212_geojson = (shared_datadir / 'zip_11212_geojson.json').read_text()
    crude_albany_geojson = (shared_datadir / 'crude_albany_geojson.json').read_text()
    new_mexico_state_geojson = (shared_datadir / 'new_mexico_state_geojson.json').read_text()
    crude_new_york_county_geojson = (shared_datadir / 'crude_new_york_county_geojson.json').read_text()

    # A crudely-drawn polygon that approximates the shape of the
    # continental United States.  The goal is to be good enough to be
    # used in a test, without creating a complex object that will slow
    # down the test.
    crude_us_geojson = (shared_datadir / 'crude_us_geojson.json').read_text()

    @classmethod
    def get_database_connection(cls):
        url = Configuration.database_url(test=True)
        engine, connection = SessionManager.initialize(url)

        return engine, connection

    @classmethod
    def setup_class(cls):
        cls.engine, cls.connection = cls.get_database_connection()
        os.environ['TESTING'] = 'true'

    @classmethod
    def teardown_class(cls):
        # Destroy the database connection and engine.
        cls.connection.close()
        cls.engine.dispose()
        if 'TESTING' in os.environ:
            del os.environ['TESTING']

    def setup(self):
        # Create a new connection to the database.
        self._db = Session(self.connection)
        self.transaction = self.connection.begin_nested()

        # Start with a high number so it won't interfere with tests that
        # search for a small number.
        self.counter = 2000

        self.time_counter = datetime(2014, 1, 1)

        self.latitude_counter = -90
        self.longitude_counter = -90

    def teardown(self):
        secret_keys = self._db.query(ConfigurationSetting).filter(
            ConfigurationSetting.key == Configuration.SECRET_KEY
        )
        [self._db.delete(secret_key) for secret_key in secret_keys]
        # Close the session.
        self._db.close()

        # Roll back all database changes that happened during this
        # test, whether in the session that was just closed or some
        # other session.
        self.transaction.rollback()

    @property
    def _id(self):
        self.counter += 1
        return self.counter

    @property
    def _str(self):
        return str(self._id)

    @property
    def _url(self):
        return "https://%s/" % self._str

    @property
    def _time(self):
        v = self.time_counter
        self.time_counter = self.time_counter + timedelta(days=1)
        return v

    def _admin(self, username=None, password=None):
        username = username or "Admin"
        password = password or "123"
        return Admin.authenticate(self._db, username, password)

    def _library(self, name=None, short_name=None, eligibility_areas=[], focus_areas=[], audiences=None,
                 library_stage=Library.PRODUCTION_STAGE, registry_stage=Library.PRODUCTION_STAGE,
                 has_email=False, description=None):
        name = name or self._str
        library, ignore = get_one_or_create(
            self._db, Library, name=name,
            create_method_kwargs=dict(
                authentication_url=self._url,
                opds_url=self._url
            )
        )
        library.short_name = short_name or self._str
        library.shared_secret = self._str
        library.description = description or self._str
        for place in eligibility_areas:
            get_one_or_create(self._db, ServiceArea, library=library,
                              place=place, type=ServiceArea.ELIGIBILITY)
        for place in focus_areas:
            get_one_or_create(self._db, ServiceArea, library=library,
                              place=place, type=ServiceArea.FOCUS)
        audiences = audiences or [Audience.PUBLIC]
        library.audiences = [Audience.lookup(self._db, audience) for audience in audiences]
        library.library_stage = library_stage
        library.registry_stage = registry_stage
        if has_email:
            library.set_hyperlink(Hyperlink.INTEGRATION_CONTACT_REL, "mailto:" + name + "@library.org")
            library.set_hyperlink(Hyperlink.HELP_REL, "mailto:" + name + "@library.org")
            library.set_hyperlink(Hyperlink.COPYRIGHT_DESIGNATED_AGENT_REL, "mailto:" + name + "@library.org")
        return library

    def _external_integration(self, protocol, goal=None, settings=None,
                              libraries=None, **kwargs):
        integration = None
        if not libraries:
            integration, ignore = get_one_or_create(
                self._db, ExternalIntegration, protocol=protocol, goal=goal
            )
        else:
            if not isinstance(libraries, list):
                libraries = [libraries]

            # Try to find an existing integration for one of the given
            # libraries.
            for library in libraries:
                integration = ExternalIntegration.lookup(
                    self._db, protocol, goal, library=libraries[0]
                )
                if integration:
                    break

            if not integration:
                # Otherwise, create a brand new integration specifically
                # for the library.
                integration = ExternalIntegration(
                    protocol=protocol, goal=goal,
                )
                integration.libraries.extend(libraries)
                self._db.add(integration)

        for attr, value in list(kwargs.items()):
            setattr(integration, attr, value)

        settings = settings or dict()
        for key, value in list(settings.items()):
            integration.set_setting(key, value)

        return integration

    def _place(self, external_id=None, external_name=None, type=None,
               abbreviated_name=None, parent=None, geometry=None):
        if not geometry:
            geometry = 'SRID=4326;POINT(%s %s)' % (
                self.latitude_counter, self.longitude_counter
            )
            self.latitude_counter += 0.1
            self.longitude_counter += 0.1
        elif isinstance(geometry, str):
            # Treat it as GeoJSON.
            geometry = GeometryUtility.from_geojson(geometry)
        external_id = external_id or self._str
        external_name = external_name or self._str
        type = type or Place.CITY
        place, is_new = get_one_or_create(
            self._db, Place, external_id=external_id,
            external_name=external_name, type=type,
            abbreviated_name=abbreviated_name, parent=parent,
        )
        place.geometry = geometry
        self._db.commit()
        return place

    # Some useful Libraries.
    @property
    def nypl(self):
        return self._library("NYPL", "nypl", [self.new_york_city, self.zip_11212], has_email=True)

    @property
    def connecticut_state_library(self):
        return self._library("Connecticut State Library",
                             "CT",
                             [self.connecticut_state],
                             has_email=True)

    @property
    def kansas_state_library(self):
        return self._library(
            "Kansas State Library",
            "KS",
            [self.kansas_state],
            has_email=True
        )

    # Some useful Places.

    @property
    def crude_us(self):
        """A Place representing the United States. Unlike other Places in this
        series, this is backed by a crude GeoJSON drawing of the
        continental United States, not the much more complex GeoJSON
        that would be obtained from an official source. This shape
        includes large chunks of ocean, as well as portions of Canada
        and Mexico.
        """
        return self._place('US', 'United States', Place.NATION,
                           'US', None, self.crude_us_geojson)

    @property
    def new_york_state(self):
        return self._place('36', 'New York', Place.STATE,
                           'NY', self.crude_us, self.new_york_state_geojson)

    @property
    def connecticut_state(self):
        return self._place('09', 'Connecticut', Place.STATE,
                           'CT', self.crude_us, self.connecticut_state_geojson)

    @property
    def new_york_city(self):
        place = self._place('365100', 'New York', Place.CITY,
                            None, self.new_york_state,
                            self.new_york_city_geojson)
        alias = get_one_or_create(
            self._db, PlaceAlias, place=place, name='Manhattan'
        )
        alias = get_one_or_create(
            self._db, PlaceAlias, place=place, name='Brooklyn'
        )
        alias = get_one_or_create(
            self._db, PlaceAlias, place=place, name='New York'
        )
        return place

    @property
    def crude_kings_county(self):
        """A Place representing Kings County, NY. Unlike other Places in this
        series, this is backed by a crude GeoJSON drawing of Kings
        County, not the much more complex GeoJSON that would be
        obtained from an official source.
        """
        return self._place('Kings', 'Kings', Place.COUNTY,
                           None, self.new_york_state,
                           self.crude_kings_county_geojson)

    @property
    def kansas_state(self):
        return self._place('20', 'Kansas', Place.STATE,
                           'KS', self.crude_us, self.kansas_state_geojson)

    @property
    def massachussets_state(self):
        return self._place('25', 'Massachussets', Place.STATE,
                           'MA', self.crude_us, None)

    @property
    def boston_ma(self):
        return self._place('2507000', 'Boston', Place.CITY,
                           None, self.massachussets_state,
                           self.boston_geojson)

    @property
    def manhattan_ks(self):
        return self._place('2044250', 'Manhattan', Place.CITY,
                           None, self.kansas_state,
                           self.manhattan_ks_geojson)

    @property
    def zip_10018(self):
        return self._place(
            '10018', '10018', Place.POSTAL_CODE, None, self.new_york_state,
            self.zip_10018_geojson
        )

    @property
    def zip_11212(self):
        place = self._place(
            '11212', '11212', Place.POSTAL_CODE, None, self.new_york_state,
            self.zip_11212_geojson
        )
        alias = get_one_or_create(
            self._db, PlaceAlias, place=place, name='Brooklyn'
        )
        return place

    @property
    def zip_12601(self):
        return self._place(
            '12601', '12601', Place.POSTAL_CODE, None, self.new_york_state,
            self.zip_12601_geojson
        )

    @property
    def crude_albany(self):
        return self._place("Albany", "Albany", Place.CITY,
                           None, self.new_york_state, self.crude_albany_geojson)

    @property
    def new_mexico_state(self):
        return self._place('NM', 'New Mexico', Place.STATE,
                           'NM', self.crude_us, self.new_mexico_state_geojson)

    @property
    def crude_new_york_county(self):
        return self._place("Manhattan", "New York County", Place.COUNTY,
                           "NY", self.new_york_state, self.crude_new_york_county_geojson)


class DummyHTTPResponse():
    def __init__(self, status_code, headers, content, links=None, url=None):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.links = links or {}
        self.url = url or "http://url/"

    @property
    def raw(self):
        return BytesIO(self.content)


class DummyHTTPClient():

    def __init__(self):
        self.responses = []
        self.requests = []

    def queue_response(self, response_code, media_type="text/html",
                       other_headers=None, content='', links=None,
                       url=None):
        headers = {}
        if media_type:
            headers["Content-Type"] = media_type
        if other_headers:
            for k, v in list(other_headers.items()):
                headers[k.lower()] = v
        self.responses.insert(
            0, DummyHTTPResponse(response_code, headers, content, links, url)
        )

    def do_get(self, url, headers=None, allowed_response_codes=None, **kwargs):
        self.requests.append(url)
        response = self.responses.pop()
        if isinstance(response.status_code, Exception):
            raise response.status_code

        # Simulate the behavior of requests, where response.url contains
        # the final URL that responded to the request.
        response.url = url

        code = response.status_code
        series = "%sxx" % (code // 100)

        if allowed_response_codes and (code not in allowed_response_codes and series not in allowed_response_codes):
            raise BadResponseException(url, "Bad Response!", status_code=code)
        return response


class MockRequestsResponse():
    """A mock object that simulates an HTTP response from the
    `requests` library.
    """
    def __init__(self, status_code, headers={}, content=None, url=None):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.url = url or "http://url/"

    def json(self):
        content = self.content
        # The queued content might be a JSON string or it might
        # just be the object you'd get from loading a JSON string.
        if isinstance(content, (bytes, str)):
            content = json.loads(self.content)
        return content

    @property
    def text(self):
        return self.content.decode("utf8")


class MockPlace():
    """Used to test AuthenticationDocument.parse_coverage."""

    # Used to indicate that a place name is ambiguous.
    AMBIGUOUS = object()

    # Used to indicate coverage through the universe or through a
    # country.
    EVERYWHERE = object()

    # Used within a test to provide a starting point for place
    # names that don't mention a nation.
    _default_nation = None

    by_name = dict()

    def __init__(self, inside=None):
        self.inside = inside or dict()
        self.abbreviated_name = None

    @classmethod
    def default_nation(cls, _db):
        return cls._default_nation

    @classmethod
    def lookup_one_by_name(cls, _db, name, place_type):
        place = cls.by_name.get(name)
        if place is cls.AMBIGUOUS:
            raise MultipleResultsFound()
        if place is None:
            raise NoResultFound()
        print("%s->%s" % (name, place))
        return place

    def lookup_inside(self, name):
        place = self.inside.get(name)
        if place is self.AMBIGUOUS:
            raise MultipleResultsFound()
        if place is None:
            raise NoResultFound()
        return place

    @classmethod
    def everywhere(cls, _db):
        return cls.EVERYWHERE
