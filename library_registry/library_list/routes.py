from flask import Blueprint, current_app

from library_registry.decorators import (
    compressible,
    has_library,
    returns_json_or_response_or_problem_detail,
    returns_problem_detail,
    uses_location,
)

libr_list = Blueprint('libr_list', __name__)

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