from flask import Blueprint, render_template, request, Response, current_app

from library_registry.decorators import (
    compressible,
    has_library,
    returns_json_or_response_or_problem_detail,
    returns_problem_detail,
    uses_location,
)

libr = Blueprint('libr', __name__)

@libr.route('/')
@uses_location
@returns_problem_detail
def nearby(_location):
    return current_app.library_registry.registry_controller.nearby(_location)

@libr.route('/qa')
@uses_location
@returns_problem_detail
def nearby_qa(_location):
    return current_app.library_registry.registry_controller.nearby(_location, live=False)

@libr.route("/register", methods=["GET", "POST"])
@returns_problem_detail
def register():
    return current_app.library_registry.registry_controller.register()

@libr.route('/search')
@uses_location
@returns_problem_detail
def search(_location):
    return current_app.library_registry.registry_controller.search(_location)

@libr.route('/qa/search')
@uses_location
@returns_problem_detail
def search_qa(_location):
    return current_app.library_registry.registry_controller.search(
        _location, live=False
    )

@libr.route('/confirm/<int:resource_id>/<secret>')
@returns_problem_detail
def confirm_resource(resource_id, secret):
    return current_app.library_registry.validation_controller.confirm(resource_id, secret)

@libr.route('/libraries')
@compressible
@uses_location
@returns_problem_detail
def libraries_opds(_location=None):
    return current_app.library_registry.registry_controller.libraries_opds(location=_location)

@libr.route('/libraries/qa')
@compressible
@uses_location
@returns_problem_detail
def libraries_qa(_location=None):
    return current_app.library_registry.registry_controller.libraries_opds(location=_location, live=False)

@libr.route('/library/<uuid>')
@has_library
@returns_json_or_response_or_problem_detail
def library():
    return current_app.library_registry.registry_controller.library()

@libr.route('/library/<uuid>/eligibility')
@has_library
@returns_problem_detail
def library_eligibility():
    return current_app.library_registry.coverage_controller.eligibility_for_library()

@libr.route('/library/<uuid>/focus')
@has_library
@returns_problem_detail
def library_focus():
    return current_app.library_registry.coverage_controller.focus_for_library()

@libr.route('/coverage')
@returns_problem_detail
def coverage():
    return current_app.library_registry.coverage_controller.lookup()

@libr.route('/heartbeat')
@returns_problem_detail
def hearbeat():
    return current_app.library_registry.heartbeat.heartbeat()