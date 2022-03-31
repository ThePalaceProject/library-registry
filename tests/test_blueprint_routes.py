import uuid
from werkzeug.datastructures import MultiDict
#from flask import Flask, Blueprint, Response, Request, request
from library_registry.controller import LibraryRegistryController

import pytest
import flask
from tests.conftest import *

from library_registry.drm.controller import AdobeVendorIDClient
from library_registry.decorators import has_library, uses_location
from tests.test_controller import MockEmailer, MockLibraryRegistry



class TestAdminBlueprintRoutes:
    
    def test_admin_admin_view(self, app):
        client = app.test_client()
        status_code = client.get('/admin/').status
        assert status_code == "200 OK"
    
    def test_admin_log_in(self, app):
        client = app.test_client()
        status_code = client.post('/admin/log_in', data=dict(username="test_username", password="test_password")).status
        assert status_code == "302 FOUND"

    def test_admin_log_out(self, app):
        client = app.test_client()
        status_code = client.get('/admin/log_out').status
        assert status_code == "302 FOUND"

    def test_admin_libraries(self, app):
        client = app.test_client()
        status_code = client.get('/admin/libraries').status
        assert status_code == "200 OK"

    def test_admin_libraries_qa_admin(self, app):
        client = app.test_client()
        status_code = client.get('/admin/libraries/qa').status
        assert status_code == "200 OK"
    
    def test_admin_library_details(self):
        ''' See test_controller.py, test_library_details()'''
        return True

    def test_admin_search_details(self):
        ''' See test_controller.py, test_search_details()'''
        return True

    def test_admin_validate_email(self):
        ''' See test_controller.py, test_validate_email()'''
        return True
        
    def test_admin_edit_registration(self):
        ''' See test_controller.py, test_edit_registration()'''
        return True
    
    def test_admin_pls_id(self, app, mock_registry_controller, nypl):
        uuid = nypl.internal_urn.split("uuid:")[1]
        with app.test_request_context("/", method="POST"):
            flask.request.form = MultiDict([
                ("uuid", uuid),
                ("pls_id", "12345")
            ])
            response = mock_registry_controller.add_or_edit_pls_id()
        assert response.status_code == 200

class TestDRMBlueprintRoutes:
    
    def test_adobe_vendor_id_signin(self):
        return True

    def test_adobe_vendor_id_accountinfo(self):
        ''' See test_adobe_vendor_id.py, test_handle_accountinfo_success()'''
        return True

    def test_adobe_vendor_id_status(self):
        return True


@pytest.fixture
def mock_registry(db_session):
    library_registry = MockLibraryRegistry(db_session, testing=True, emailer_class=MockEmailer)
    yield library_registry


@pytest.fixture
def mock_registry_controller(mock_registry):
    registry_controller = LibraryRegistryController(mock_registry, emailer_class=MockEmailer)
    yield registry_controller


class TestLibraryProtocolBlueprintRoutes:
    
    def test_library_protocol_nearby(self, app, mock_registry_controller, places):
        return True

    def test_library_protocol_nearby_qa(self):
        return True
    
    def test_library_protocol_register(self, app):
        client = app.test_client()
        status_code = client.get('/register').status
        assert status_code == "200 OK"

    def test_library_protocol_register_post(self, app):
        #client = app.test_client()
        #status_code = client.post('/register', data=dict(url="http://nypl.org", contact="test_contact", stage="test_stage")).status
        #assert status_code == "200 OK"
        return True
    
    def test_library_protocol_search(self):
        return True
    
    def test_library_protocol_search_qa(self):
        return True
    
    def test_library_protocol_confirm_resource(self):
        return True
    
    def test_library_protocol_libraries_opds(self, app):
        client = app.test_client()
        status_code = client.get('/libraries').status
        assert status_code == "200 OK"
    
    def test_library_protocol_libraries_qa(self, app):
        client = app.test_client()
        status_code = client.get('/libraries/qa').status
        assert status_code == "200 OK"
    
    @has_library
    def test_library_protocol_library(self, app):
        client = app.test_client()
        test_uuid = str(uuid.uuid4())
        endpoint_url = '/library/' + test_uuid
        status_code = client.get(endpoint_url).status
        assert status_code == "200 OK"
    
    @has_library
    def test_library_protocol_library_eligibility(self, app):
        client = app.test_client()
        test_uuid = str(uuid.uuid4())
        endpoint_url = '/libr.library/' + test_uuid + '/eligibility'
        status_code = client.get(endpoint_url).status
        assert status_code == "200 OK"
    
    @has_library
    def test_library_protocol_library_focus(self, app):
        client = app.test_client()
        test_uuid = str(uuid.uuid4())
        endpoint_url = '/library/' + test_uuid + '/focus'
        status_code = client.get(endpoint_url).status
        assert status_code == "200 OK"
    
    def test_library_protocol_coverage(self, app):
        #client = app.test_client()
        #status_code = client.get('/coverage').status
        #assert status_code == "200 OK"
        return True
    
    def test_library_protocol_heartbeat(self, app):
        client = app.test_client()
        status_code = client.get('/heartbeat').status
        assert status_code == "200 OK"