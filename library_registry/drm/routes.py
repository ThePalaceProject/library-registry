from flask import Blueprint, Response, current_app

from library_registry.decorators import (
    returns_problem_detail,
)

drm = Blueprint(
    'drm', __name__,
    template_folder='templates')

# Adobe Vendor ID implementation
@drm.route('/AdobeAuth/SignIn', methods=['POST'])
@returns_problem_detail
def adobe_vendor_id_signin():
    if current_app.library_registry.drm.adobe_vendor_id:
        return current_app.library_registry.drm.adobe_vendor_id.signin_handler()
    else:
        return Response("", 404)

@drm.route('/AdobeAuth/AccountInfo', methods=['POST'])
@returns_problem_detail
def adobe_vendor_id_accountinfo():
    if current_app.library_registry.drm.adobe_vendor_id:
        return current_app.library_registry.drm.adobe_vendor_id.userinfo_handler()
    else:
        return Response("", 404)

@drm.route('/AdobeAuth/Status')
@returns_problem_detail
def adobe_vendor_id_status():
    if current_app.library_registry.drm.adobe_vendor_id:
        return current_app.library_registry.drm.adobe_vendor_id.status_handler()
    else:
        return Response("", 404)