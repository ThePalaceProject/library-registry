import logging

import psycopg2

from alembic.command import stamp, upgrade
from alembic.config import Config


def migrate(db_url: str):
    """Ensure the alembic migration state is up-to-date.
    If the database table "libraries" has not been created yet, we can assume this is a new deployment.
    Else, we can assume this database should attempt an upgrade to the latest version, if the DB
    is already at the latest version, alembic will ignore the upgrade command.

    Note: This function must be run before the SQLAlchemy session is initialized.
    """
    log = logging.getLogger(__name__)
    logging.basicConfig()
    log.setLevel(logging.INFO)

    # Find the 'libraries' table
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pg_catalog.pg_tables where tablename='libraries';")
    table_row = cursor.fetchone()
    cursor.close()
    conn.close()

    alembic_cfg = Config("alembic.ini")
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
