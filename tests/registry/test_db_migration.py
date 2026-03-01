from unittest import mock

import psycopg2

from alembic.command import ensure_version
from alembic.config import Config
from palace.registry.config import Configuration
from palace.registry.db_migration import migrate


class TestDBMigrate:
    @mock.patch("palace.registry.db_migration.psycopg2.connect")
    @mock.patch("palace.registry.db_migration.stamp")
    @mock.patch("palace.registry.db_migration.upgrade")
    def test_migrate(self, mock_upgrade, mock_stamp, mock_connect):
        fetchone = mock_connect().cursor().fetchone

        # New DB, No tables
        # Should just 'stamp head'
        fetchone.return_value = None
        migrate("postgresql://...")
        assert mock_stamp.call_count == 1
        assert mock_upgrade.call_count == 0

        mock_upgrade.reset_mock()
        mock_stamp.reset_mock()

        # Existing DB, Tables available
        # Should attempt to run an upgrade
        fetchone.return_value = ("public", "libraries", None, None)
        migrate("postgresql://...")
        assert mock_stamp.call_count == 0
        assert mock_upgrade.call_count == 1

    def test_migrate_bad_stamp(self):
        """Test the alembic migration with a stamp that does not exist.
        This might happen when a rollback occurs and the stamp is of the rolled-back change
        """
        url = Configuration.database_url(test=True)
        conn = psycopg2.connect(url)
        cursor = conn.cursor()

        # Create the alembic table
        cfg = Config("alembic.ini")
        ensure_version(cfg)

        # Remove any alembic versions that existed from previous tests
        cursor.execute("DELETE from alembic_version")
        # Set a fake alembic version, ensuring the 'upgrade' will fail
        cursor.execute("INSERT INTO alembic_version(version_num) VALUES ('xxxxx')")
        conn.commit()

        # This will not raise an error
        # But it will not change the "head" either
        migrate(url)

        cursor.execute("SELECT * from alembic_version")
        version = cursor.fetchone()
        assert version == ("xxxxx",)
