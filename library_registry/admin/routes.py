from datetime import datetime, timezone, timedelta
from flask import Blueprint, current_app, make_response

from flask_jwt_extended import jwt_required, get_jwt, create_access_token, get_jwt_identity, set_access_cookies

from library_registry.admin.decorators import check_logged_in

from library_registry.decorators import (
    returns_json_or_response_or_problem_detail,
    returns_problem_detail,
)

admin = Blueprint(
    'admin', __name__,
    template_folder='templates')

# Using an `after_request` callback, we refresh any token that is within 30
# minutes of expiring. Change the timedeltas to match the needs of your application.


@admin.after_request
def refresh_expiring_jwts(response):
    try:
        exp_timestamp = get_jwt()["exp"]
        now = datetime.now(timezone.utc)
        target_timestamp = datetime.timestamp(now + timedelta(minutes=30))
        if target_timestamp > exp_timestamp:
            access_token = create_access_token(identity=get_jwt_identity())
            response = make_response(response, 201)
            set_access_cookies(response, access_token)
        return response
    except (RuntimeError, KeyError):
        # Case where there is not a valid JWT. Just return the original response
        return response


@admin.route('/admin/', strict_slashes=False)
def admin_view():
    return current_app.library_registry.view_controller()


@admin.route('/admin/log_in', methods=["POST"])
@admin.route('/admin/log_in/<jwt_preferred>')
@returns_problem_detail
def log_in(jwt_preferred=False):
    return current_app.library_registry.admin_controller.log_in(jwt_preferred)


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
