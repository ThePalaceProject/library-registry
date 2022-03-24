from flask import Flask, Blueprint, Response
from library_registry.adobe_vendor_id import AdobeVendorIDClient
from library_registry.decorators import has_library, uses_location

import pytest
import tests.conftest
import uuid

class TestAdminBlueprintRoutes:
    
    def test_admin_admin_view(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        status_code = client.get('/admin/').status
        assert status_code == "200 OK"
    
    def test_admin_log_in(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        status_code = client.post('/admin/log_in', data=dict(username="test_username", password="test_password")).status
        assert status_code == "200 OK"

    def test_admin_log_out(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        status_code = client.get('/admin/log_out').status
        assert status_code == "200 OK"

    def test_admin_libraries(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        status_code = client.get('/admin/libraries').status
        assert status_code == "200 OK"

    def test_admin_libraries_qa_admin(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        status_code = client.get('/admin/libraries/qa').status
        assert status_code == "200 OK"
    
    def test_admin_library_details(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        test_uuid = uuid.uuid4()
        status_code = client.post('/admin/libraries/email', data=dict(uuid=test_uuid)).status
        assert status_code == "200 OK"

    def test_admin_search_details(self, app_with_blueprints, new_york_state):
        client = app_with_blueprints.test_client()
        place_name = new_york_state.external_name
        status_code = client.post('/admin/libraries/search_details', data=dict(name=place_name)).status
        assert status_code == "200 OK"

    def test_admin_validate_email(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        status_code = client.post('/admin/libraries/email', json={"email":"email"}).status
        assert status_code == "200 OK"

    def test_admin_edit_registration(self):
        return True
    
    def test_admin_pls_id(self):
        return True

class TestDRMBlueprintRoutes:
    
    def test_adobe_vendor_id_signin(self):
        return True

    def test_adobe_vendor_id_accountinfo(self):
        return True

    def test_adobe_vendor_id_status(self):
        client = AdobeVendorIDClient("/AdobeAuth/Status")
        status = client.status()
        assert status == 'UP'
    
class TestLibraryProtocolBlueprintRoutes:
    
    @uses_location
    def test_library_protocol_nearby(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        status_code = client.get('/').status
        assert status_code == "200 OK"

    def test_library_protocol_nearby_qa(self):
        return True
    
    def test_library_protocol_register(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        status_code = client.get('/register').status
        assert status_code == "200 OK"

    def test_library_protocol_register_post(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        status_code = client.post('/register', data=dict(url="http://nypl.org", contact="test_contact", stage="test_stage")).status
        assert status_code == "200 OK"
    
    def test_library_protocol_search(self):
        return True
    
    def test_library_protocol_search_qa(self):
        return True
    
    def test_library_protocol_confirm_resource(self):
        return True
    
    def test_library_protocol_libraries_opds(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        status_code = client.get('/libraries').status
        assert status_code == "200 OK"
    
    def test_library_protocol_libraries_qa(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        status_code = client.get('/libraries/qa').status
        assert status_code == "200 OK"
    
    @has_library
    def test_library_protocol_library(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        test_uuid = str(uuid.uuid4())
        endpoint_url = '/library/' + test_uuid
        status_code = client.get(endpoint_url).status
        assert status_code == "200 OK"
    
    @has_library
    def test_library_protocol_library_eligibility(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        test_uuid = str(uuid.uuid4())
        endpoint_url = '/library/' + test_uuid + '/eligibility'
        status_code = client.get(endpoint_url).status
        assert status_code == "200 OK"
    
    @has_library
    def test_library_protocol_library_focus(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        test_uuid = str(uuid.uuid4())
        endpoint_url = '/library/' + test_uuid + '/focus'
        status_code = client.get(endpoint_url).status
        assert status_code == "200 OK"
    
    def test_library_protocol_coverage(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        status_code = client.get('/coverage').status
        assert status_code == "200 OK"
    
    def test_library_protocol_heartbeat(self, app_with_blueprints):
        client = app_with_blueprints.test_client()
        status_code = client.get('/heartbeat').status
        assert status_code == "200 OK"