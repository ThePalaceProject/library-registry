from flask import Blueprint, render_template, request, Response, current_app

from library_registry.decorators import (
    compressible,
    has_library,
    returns_json_or_response_or_problem_detail,
    returns_problem_detail,
    uses_location,
)

drm = Blueprint('drm', __name__)

# Adobe Vendor ID implementation
@drm.route('/AdobeAuth/SignIn', methods=['POST'])
@returns_problem_detail
def adobe_vendor_id_signin():
    if current_app.library_registry.adobe_vendor_id:
        return current_app.library_registry.adobe_vendor_id.signin_handler()
    else:
        return Response("", 404)

@drm.route('/AdobeAuth/AccountInfo', methods=['POST'])
@returns_problem_detail
def adobe_vendor_id_accountinfo():
    if current_app.library_registry.adobe_vendor_id:
        return current_app.library_registry.adobe_vendor_id.userinfo_handler()
    else:
        return Response("", 404)

@drm.route('/AdobeAuth/Status')
@returns_problem_detail
def adobe_vendor_id_status():
    if current_app.library_registry.adobe_vendor_id:
        return current_app.library_registry.adobe_vendor_id.status_handler()
    else:
        return Response("", 404)