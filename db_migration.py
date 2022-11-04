# In order to keep the DB from initializing accidentally we do not import any other part of the application in this file
import logging
import os

import psycopg2

from alembic.command import stamp, upgrade
from alembic.config import Config


def migrate():
    """Ensure the alembic migration state is up-to-date.
    If the database table "libraries" has not been created yet, we can assume this is a new deployment.
    Else, we can assume this database should attempt an upgrade to the latest version, if the DB
    is already at the latest version, alembic will ignore the upgrade command.

    Note: This function must be run before the SQLAlchemy session is initialized.
    """
    log = logging.getLogger(__name__)
    log.setLevel(logging.INFO)

    # Find the 'libraries' table
    db_url = os.environ.get("SIMPLIFIED_PRODUCTION_DATABASE")
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pg_catalog.pg_tables where tablename='libraries';")
    table_row = cursor.fetchone()

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("url", db_url)
    if table_row is None:
        # We have no libraries table setup, this is the first ever run.
        # SqlAlchemy will create all tables, simply stamp the head.
        log.info(
            "Database tables were not detected, stamping the alembic version to 'head'."
        )
        stamp(alembic_cfg, "head")
    else:
        # This is not a new deployment, run the 'upgrade head' command.
        # This is an idempotent command, we don't have to check whether it needs to be run or not.
        log.info("Running alembic upgrade.")
        upgrade(alembic_cfg, "head")
