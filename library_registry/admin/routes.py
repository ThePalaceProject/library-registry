from datetime import datetime, timezone, timedelta
from flask import Blueprint, current_app, make_response, jsonify

from flask_jwt_extended import jwt_required, get_jwt, create_access_token, get_jwt_identity, set_access_cookies, verify_jwt_in_request

from library_registry.admin.decorators import check_logged_in

from library_registry.decorators import (
    returns_json_or_response_or_problem_detail,
    returns_problem_detail,
)

admin = Blueprint(
    'admin', __name__,
    template_folder='templates')


@admin.route('/admin/', strict_slashes=False)
def admin_view():
    return current_app.library_registry.view_controller()


@admin.route('/admin/log_in', methods=["POST"])
@admin.route('/admin/log_in/<jwt_cookie_boolean>')
@returns_problem_detail
def log_in(jwt_cookie_boolean=False):
    return current_app.library_registry.admin_controller.log_in(jwt_cookie_boolean)


# We are using the `refresh=True` options in jwt_required to only allow
# refresh tokens to access this route.
@admin.route("/admin/refresh_token", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    return current_app.library_registry.admin_controller.refresh_token()


@admin.route('/admin/log_out')
@check_logged_in
@returns_problem_detail
def log_out():
    return current_app.library_registry.admin_controller.log_out()


@admin.route('/admin/libraries')
@jwt_required(optional=True)
@check_logged_in
@returns_json_or_response_or_problem_detail
def libraries():
    return current_app.library_registry.admin_controller.libraries()


@admin.route('/admin/libraries/qa')
@check_logged_in
@returns_json_or_response_or_problem_detail
def libraries_qa_admin():
    return current_app.library_registry.admin_controller.libraries(live=False)


@admin.route('/admin/libraries/<uuid>')
@check_logged_in
@returns_json_or_response_or_problem_detail
def library_details(uuid):
    return current_app.library_registry.admin_controller.library_details(uuid)


@admin.route('/admin/libraries/search_details', methods=["POST"])
@check_logged_in
@returns_json_or_response_or_problem_detail
def search_details():
    return current_app.library_registry.admin_controller.search_details()


@admin.route('/admin/libraries/email', methods=["POST"])
@check_logged_in
@returns_json_or_response_or_problem_detail
def validate_email():
    return current_app.library_registry.admin_controller.validate_email()


@admin.route('/admin/libraries/registration', methods=["POST"])
@check_logged_in
@returns_json_or_response_or_problem_detail
def edit_registration():
    return current_app.library_registry.admin_controller.edit_registration()


@admin.route('/admin/libraries/pls_id', methods=["POST"])
@check_logged_in
@returns_json_or_response_or_problem_detail
def pls_id():
    return current_app.library_registry.admin_controller.add_or_edit_pls_id()
