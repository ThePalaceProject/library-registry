"""
The functions defined here will, if copied into conftest.py, alter the overall behavior
of pytest during test runs. They are for debugging, and should never be left active in a
commit.
"""

# The following functions work together to let you detect which tests are
# leaving artifacts in the database. However, they spit out a lot of extra per-test
# console output, so only uncomment them if you need the functionality.

# def pytest_report_teststatus(report, config):
#     """Removes the dot per test from output"""
#     return report.outcome, "", report.outcome.upper()

# @pytest.fixture(autouse=True, scope="function")
# def how_many_audiences(db_session, capsys):
#     yield 1       # this makes the next bit happen *after* the test body
#     count = db_session.query(Audience).count()
#     test_name = os.environ.get('PYTEST_CURRENT_TEST')
#     with capsys.disabled():
#         print(f"### Audience COUNT after {test_name}: {count}")


# @pytest.fixture(autouse=True, scope="function")
# def how_many_config_settings(db_session, capsys):
#     yield 1       # this makes the next bit happen *after* the test body
#     count = db_session.query(ConfigurationSetting).count()
#     test_name = os.environ.get('PYTEST_CURRENT_TEST')
#     with capsys.disabled():
#         print(f"### CS COUNT after {test_name}: {count}")
#
#
# @pytest.fixture(autouse=True, scope="function")
# def how_many_places(db_session, capsys):
#     yield 1       # this makes the next bit happen *after* the test body
#     count = db_session.query(Place).count()
#     test_name = os.environ.get('PYTEST_CURRENT_TEST')
#     with capsys.disabled():
#         print(f"### PLACE COUNT after {test_name}: {count}")
