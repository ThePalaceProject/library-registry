import os
from typing import Optional

import pytest

from admin.config import Configuration as AdminConfig
from admin.config import OperationalMode


class TestAdminUI(object):

    @staticmethod
    def _set_env(key: str, value: Optional[str]):
        if value:
            os.environ[key] = value
        elif key in os.environ:
            del os.environ[key]

    @pytest.mark.parametrize(
        'package_name, package_version, mode, expected_result',
        [
            [None, None, OperationalMode.production,
             'https://cdn.jsdelivr.net/npm/@thepalaceproject/library-registry-admin@'],
            ['@some-scope/some-package', '1.0.0', OperationalMode.production,
             'https://cdn.jsdelivr.net/npm/@some-scope/some-package@1.0.0'],
            ['some-package', '1.0.0', OperationalMode.production,
             'https://cdn.jsdelivr.net/npm/some-package@1.0.0'],
            [None, None, OperationalMode.development, '/'],
            [None, '1.0.0', OperationalMode.development, '/'],
            ['some-package', '1.0.0', OperationalMode.development, '/'],
        ])
    def test_package_url(self, package_name: Optional[str], package_version: Optional[str],
                         mode: OperationalMode, expected_result: str):
        self._set_env('LIBRARY_REGISTRY_ADMIN_PACKAGE_NAME', package_name)
        self._set_env('LIBRARY_REGISTRY_ADMIN_PACKAGE_VERSION', package_version)
        result = AdminConfig.package_url(_operational_mode=mode)
        assert result.startswith(expected_result)

    @pytest.mark.parametrize(
        'package_name, package_version, expected_result',
        [
            [None, None, '/my-base-dir/node_modules/@thepalaceproject/library-registry-admin'],
            [None, '1.0.0', '/my-base-dir/node_modules/@thepalaceproject/library-registry-admin'],
            ['some-package', '1.0.0', '/my-base-dir/node_modules/some-package'],
        ])
    def test_package_development_directory(self, package_name: Optional[str], package_version: Optional[str],
                                           expected_result: str):
        self._set_env('LIBRARY_REGISTRY_ADMIN_PACKAGE_NAME', package_name)
        self._set_env('LIBRARY_REGISTRY_ADMIN_PACKAGE_VERSION', package_version)
        result = AdminConfig.package_development_directory(_base_dir='/my-base-dir')
        assert result == expected_result
