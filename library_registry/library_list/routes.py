from flask import Blueprint, current_app

from library_registry.decorators import (
    compressible,
    has_library,
    returns_json_or_response_or_problem_detail,
    returns_problem_detail,
    uses_location,
)

libr_list = Blueprint('libr_list', __name__)

@libr_list.route('/')
@uses_location
@returns_problem_detail
def nearby(_location):
    return current_app.library_registry.registry_controller.nearby(_location)

@libr_list.route('/qa')
@uses_location
@returns_problem_detail
def nearby_qa(_location):
    return current_app.library_registry.registry_controller.nearby(_location, live=False)

@libr_list.route('/search')
@uses_location
@returns_problem_detail
def search(_location):
    return current_app.library_registry.registry_controller.search(_location)

@libr_list.route('/qa/search')
@uses_location
@returns_problem_detail
def search_qa(_location):
    return current_app.library_registry.registry_controller.search(
        _location, live=False
    )

@libr_list.route('/library/<uuid>/eligibility')
@has_library
@returns_problem_detail
def library_eligibility():
    return current_app.library_registry.coverage_controller.eligibility_for_library()

@libr_list.route('/library/<uuid>/focus')
@has_library
@returns_problem_detail
def library_focus():
    return current_app.library_registry.coverage_controller.focus_for_library()

@libr_list.route('/coverage')
@returns_problem_detail
def coverage():
    return current_app.library_registry.coverage_controller.lookup()

@libr_list.route('/libraries')
@compressible
@uses_location
@returns_problem_detail
def libraries_opds(_location=None):
    return current_app.library_registry.registry_controller.libraries_opds(location=_location)

@libr_list.route('/libraries/qa')
@compressible
@uses_location
@returns_problem_detail
def libraries_qa(_location=None):
    return current_app.library_registry.registry_controller.libraries_opds(location=_location, live=False)

@libr_list.route('/library/<uuid>')
@has_library
@returns_json_or_response_or_problem_detail
def library():
    return current_app.library_registry.registry_controller.library()