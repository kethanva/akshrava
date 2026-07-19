"""initial production schema

Revision ID: 20260719_01
Revises:
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa

revision = "20260719_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "devices",
        sa.Column("device_id", sa.String(length=128), primary_key=True),
        sa.Column("calibration_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "calibration_profiles",
        sa.Column("calibration_id", sa.String(length=128), primary_key=True),
        sa.Column("focal_px", sa.Float(), nullable=False),
        sa.Column("camera_height_m", sa.Float(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("frame_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("bearing", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=True),
        sa.Column("range_band", sa.String(length=16), nullable=True),
        sa.Column("message_key", sa.String(length=64), nullable=True),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_alert_events_device_id", "alert_events", ["device_id"])
    op.create_index("ix_alert_events_created_at", "alert_events", ["created_at"])


def downgrade():
    op.drop_table("alert_events")
    op.drop_table("calibration_profiles")
    op.drop_table("devices")
