import pytest
from flask import Flask, make_response

import admin
from admin.config import Configuration as AdminUiConfig
from util.app_server import ApplicationVersionController


@pytest.mark.parametrize(
    "version,commit,branch,ui_version,ui_package",
    [("123", "xyz", "abc", "def", "ghi"), (None, None, None, None, None)],
)
def test_application_version_controller(
    version, commit, branch, ui_version, ui_package, monkeypatch
):

    # Mock the cm version strings
    monkeypatch.setattr(admin, "__version__", version)
    monkeypatch.setattr(admin, "__commit__", commit)
    monkeypatch.setattr(admin, "__branch__", branch)

    # Mock the admin ui version strings
    if ui_package:
        monkeypatch.setenv(AdminUiConfig.ENV_ADMIN_UI_PACKAGE_NAME, ui_package)
    else:
        monkeypatch.delenv(AdminUiConfig.ENV_ADMIN_UI_PACKAGE_NAME, raising=False)

    if ui_version:
        monkeypatch.setenv(AdminUiConfig.ENV_ADMIN_UI_PACKAGE_VERSION, ui_version)
    else:
        monkeypatch.delenv(AdminUiConfig.ENV_ADMIN_UI_PACKAGE_VERSION, raising=False)

    with Flask(__name__).test_request_context("/version.json"):
        response = make_response(ApplicationVersionController.version())

    assert response.status_code == 200
    assert response.headers.get("Content-Type") == "application/json"

    assert response.json["version"] == version
    assert response.json["commit"] == commit
    assert response.json["branch"] == branch

    # When the env are not set (None) we use defaults
    assert (
        response.json["admin_ui"]["package"] == ui_package
        if ui_package
        else AdminUiConfig.PACKAGE_NAME
    )
    assert (
        response.json["admin_ui"]["version"] == ui_version
        if ui_version
        else AdminUiConfig.PACKAGE_VERSION
    )
