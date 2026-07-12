"""Add non-unique device platform metadata.

Revision ID: 0002_device_platform_metadata
Revises: 0001_initial
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_device_platform_metadata"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("devices")}
    columns = (
        sa.Column("manufacturer", sa.String(length=50), nullable=True),
        sa.Column("device_model", sa.String(length=100), nullable=True),
        sa.Column("security_patch", sa.String(length=20), nullable=True),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("devices", column)


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("devices")}
    for name in ("security_patch", "device_model", "manufacturer"):
        if name in existing:
            op.drop_column("devices", name)
