"""add calibration reference_height_px

Revision ID: 20260721_01
Revises: 20260719_01
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa

revision = "20260721_01"
down_revision = "20260719_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "calibration_profiles",
        sa.Column("reference_height_px", sa.Integer(), nullable=False, server_default="480"),
    )


def downgrade():
    op.drop_column("calibration_profiles", "reference_height_px")
