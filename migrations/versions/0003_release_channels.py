"""Add beta/stable release channels without duplicating artifacts.

Revision ID: 0003_release_channels
Revises: 0002_device_platform_metadata
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_release_channels"
down_revision = "0002_device_platform_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    device_columns = {column["name"] for column in sa.inspect(bind).get_columns("devices")}
    release_columns = {column["name"] for column in sa.inspect(bind).get_columns("releases")}
    if "update_channel" not in device_columns:
        op.add_column(
            "devices",
            sa.Column(
                "update_channel", sa.String(length=20), nullable=False, server_default="stable"
            ),
        )
        op.create_index("ix_devices_update_channel", "devices", ["update_channel"])
    if "channel" not in release_columns:
        op.add_column(
            "releases",
            sa.Column("channel", sa.String(length=20), nullable=False, server_default="stable"),
        )
        op.create_index("ix_releases_channel", "releases", ["channel"])


def downgrade() -> None:
    bind = op.get_bind()
    device_columns = {column["name"] for column in sa.inspect(bind).get_columns("devices")}
    release_columns = {column["name"] for column in sa.inspect(bind).get_columns("releases")}
    if "channel" in release_columns:
        op.drop_index("ix_releases_channel", table_name="releases")
        op.drop_column("releases", "channel")
    if "update_channel" in device_columns:
        op.drop_index("ix_devices_update_channel", table_name="devices")
        op.drop_column("devices", "update_channel")
