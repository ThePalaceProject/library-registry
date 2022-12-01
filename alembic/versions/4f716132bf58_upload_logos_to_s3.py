"""upload logos to s3

Revision ID: 4f716132bf58
Revises: aa6b44e2e879
Create Date: 2022-12-01 08:22:19.889670+00:00

"""
import logging
from dataclasses import dataclass

from alembic import op
from util.file_storage import LibraryLogoStore

# revision identifiers, used by Alembic.
revision = "4f716132bf58"
down_revision = "aa6b44e2e879"
branch_labels = None
depends_on = None


@dataclass
class MockLibrary:
    id: int


def upgrade() -> None:
    """Read all logo data from the 'logo' column and push it into the S3 storage
    Subsequently, update the logo_url with the new uploaded path
    """
    # It is not recommended to use models in the migration scripts, so we use raw sql
    log = logging.getLogger("Upload logos to S3")
    log.setLevel(logging.INFO)
    connection = op.get_bind()
    result = connection.execute("SELECT id, name, logo FROM libraries;")
    for (lib_id, lib_name, lib_logo) in result:
        if lib_logo:
            log.info(f"Uploading logo for {lib_name}")
            uploaded_path = LibraryLogoStore.write_from_b64(
                MockLibrary(lib_id), lib_logo
            )
            log.info(f"Uploaded to {uploaded_path}")
            connection.execute(
                f"UPDATE libraries SET logo_url='{uploaded_path}' WHERE id={lib_id};"
            )
        else:
            log.info(f"Library {lib_name} has no logo to upload")


def downgrade() -> None:
    pass
