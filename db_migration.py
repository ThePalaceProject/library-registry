import logging
import os

import psycopg2

from alembic.command import downgrade, stamp, upgrade
from alembic.config import Config
from alembic.util.exc import CommandError
from config import Configuration


def migrate(db_url: str = None, action: str = "upgrade", version: str = "head"):
    """Ensure the alembic migration state is up-to-date.
    If the database table "libraries" has not been created yet, we can assume this is a new deployment.
    Else, we can assume this database should attempt an upgrade to the latest version, if the DB
    is already at the latest version, alembic will ignore the upgrade command.

    Note: This function must be run before the SQLAlchemy session is initialized.
    """

    if not db_url:
        db_url = Configuration.database_url("TESTING" in os.environ)

    # Need to set up some temporary logging since we haven't
    # had a chance to read the logging config from the DB
    logging.basicConfig()
    log = logging.getLogger(__name__)
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

        if action == "upgrade":
            try:
                stamp(alembic_cfg, "head")
            except (CommandError, FileNotFoundError) as ex:
                # Alembic log config disables other logs
                log.disabled = False
                log.error(f"Alembic Error: Could not run STAMP HEAD on the database")
                log.error(f"{ex.__class__.__name__}: {ex}")
        else:
            log.error(f"You cannot downgrade a database that has not yet been created.")
    else:
        # This is not a new deployment, run upgrade/downgrade
        # This is an idempotent command, we don't have to check whether it needs to be run or not.
        try:
            if action == "upgrade":
                log.info(f"Upgrading alembic to {version}...")
                upgrade(alembic_cfg, version)
            elif action == "downgrade":
                downgrade(alembic_cfg, version)
            else:
                raise Exception(f"Invalid command: {action}")

        except (CommandError, FileNotFoundError) as ex:
            # Alembics log config disables other logs
            log.disabled = False
            log.error(f"Alembic Error: Could not run UPGRADE HEAD on the database")
            log.error(f"{ex.__class__.__name__}: {ex}")
