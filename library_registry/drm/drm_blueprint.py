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
        #return current_app.library_registry.adobe_vendor_id.signin_handler()
        """Process an incoming signInRequest document."""
        __transaction = self._db.begin_nested()
        output = self.request_handler.handle_signin_request(
            request.data.decode('utf8'),
            self.model.standard_lookup,
            self.model.authdata_lookup
        )
        __transaction.commit()
        return Response(output, 200, {"Content-Type": "application/xml"})
    else:
        return Response("", 404)

@drm.route('/AdobeAuth/AccountInfo', methods=['POST'])
@returns_problem_detail
def adobe_vendor_id_accountinfo():
    if current_app.library_registry.adobe_vendor_id:
        #return current_app.library_registry.adobe_vendor_id.userinfo_handler()
        """Process an incoming userInfoRequest document."""
        output = self.request_handler.handle_accountinfo_request(
            request.data.decode('utf8'),
            self.model.urn_to_label
        )
        return Response(output, 200, {"Content-Type": "application/xml"})
    else:
        return Response("", 404)

@drm.route('/AdobeAuth/Status')
@returns_problem_detail
def adobe_vendor_id_status():
    if current_app.library_registry.adobe_vendor_id:
        #return current_app.library_registry.adobe_vendor_id.status_handler()
        return Response("UP", 200, {"Content-Type": "text/plain"})
    else:
        return Response("", 404)