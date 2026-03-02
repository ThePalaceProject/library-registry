"""Convert datetime columns to TIMESTAMP WITH TIME ZONE

Revision ID: c57a4d8f1e23
Revises: 7dc7590e1819
Create Date: 2026-02-27 00:00:00.000000+00:00

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "c57a4d8f1e23"
down_revision = "7dc7590e1819"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "libraries",
        "timestamp",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        postgresql_using="timestamp AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "validations",
        "started_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        postgresql_using="started_at AT TIME ZONE 'UTC'",
    )


def downgrade() -> None:
    op.alter_column(
        "libraries",
        "timestamp",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        postgresql_using="timestamp AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "validations",
        "started_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        postgresql_using="started_at AT TIME ZONE 'UTC'",
    )
