"""job progress

Revision ID: 0005_job_progress
Revises: 0004_toxicity_labels
Create Date: 2026-10-14 12:45:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_job_progress"
down_revision = "0004_toxicity_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("progress", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "progress")
