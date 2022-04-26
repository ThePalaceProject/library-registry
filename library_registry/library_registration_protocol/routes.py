from flask import Blueprint, current_app

from library_registry.decorators import (
    returns_problem_detail,
)

libr = Blueprint('libr', __name__)

@libr.route("/register", methods=["GET", "POST"])
@returns_problem_detail
def register():
    return current_app.library_registry.registry_controller.register()

@libr.route('/confirm/<int:resource_id>/<secret>')
@returns_problem_detail
def confirm_resource(resource_id, secret):
    return current_app.library_registry.validation_controller.confirm(resource_id, secret)

@libr.route('/heartbeat')
@returns_problem_detail
def heartbeat():
    return current_app.library_registry.heartbeat.heartbeat()