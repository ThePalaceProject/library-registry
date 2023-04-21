"""Remove admins with empty passwords

Revision ID: 9b462df21780
Revises: 4f716132bf58
Create Date: 2023-04-20 21:34:10.306771+00:00

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "9b462df21780"
down_revision = "4f716132bf58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM admins WHERE password IS NULL OR password = ''")


def downgrade() -> None:
    pass
