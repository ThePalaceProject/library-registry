from flask import Blueprint, render_template, request, Response, current_app, session, redirect, url_for

from library_registry.decorators import (
    compressible,
    has_library,
    returns_json_or_response_or_problem_detail,
    returns_problem_detail,
    uses_location,
)

from library_registry.model import (
    Admin,
    production_session,
)

from library_registry.problem_details import (
    INVALID_CREDENTIALS,
)

admin = Blueprint('admin', __name__)

@admin.route('/admin/', strict_slashes=False)
def admin_view():
    return current_app.library_registry.view_controller()

@admin.route('/admin/log_in', methods=["POST"])
@returns_problem_detail
def log_in():
    return current_app.library_registry.registry_controller.log_in()

@admin.route('/admin/log_out')
@returns_problem_detail
def log_out():
    return current_app.library_registry.registry_controller.log_out()

@admin.route('/admin/libraries')
@returns_json_or_response_or_problem_detail
def libraries():
    return current_app.library_registry.registry_controller.libraries()

@admin.route('/admin/libraries/qa')
@returns_json_or_response_or_problem_detail
def libraries_qa_admin():
    return current_app.library_registry.registry_controller.libraries(live=False)

@admin.route('/admin/libraries/<uuid>')
@returns_json_or_response_or_problem_detail
def library_details(uuid):
    return current_app.library_registry.registry_controller.library_details(uuid)

@admin.route('/admin/libraries/search_details', methods=["POST"])
@returns_json_or_response_or_problem_detail
def search_details():
    return current_app.library_registry.registry_controller.search_details()

@admin.route('/admin/libraries/email', methods=["POST"])
@returns_json_or_response_or_problem_detail
def validate_email():
    return current_app.library_registry.registry_controller.validate_email()

@admin.route('/admin/libraries/registration', methods=["POST"])
@returns_json_or_response_or_problem_detail
def edit_registration():
    return current_app.library_registry.registry_controller.edit_registration()

@admin.route('/admin/libraries/pls_id', methods=["POST"])
@returns_json_or_response_or_problem_detail
def pls_id():
    return current_app.library_registry.registry_controller.add_or_edit_pls_id()