"""add deferred retry scheduling to transport events

Revision ID: 0006_transport_retries
Revises: 0005_viewer_push_notifications
Create Date: 2026-07-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_transport_retries"
down_revision = "0005_viewer_push_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transport_events",
        sa.Column("retry_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "transport_events",
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_transport_events_available_at", "transport_events", ["available_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_transport_events_available_at", table_name="transport_events")
    op.drop_column("transport_events", "available_at")
    op.drop_column("transport_events", "retry_attempts")
