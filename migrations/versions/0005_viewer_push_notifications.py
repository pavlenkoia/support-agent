"""add viewer web push subscriptions and notification outbox

Revision ID: 0005_viewer_push_notifications
Revises: 0004_probe_session_markers
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_viewer_push_notifications"
down_revision = "0004_probe_session_markers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "viewer_push_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("expiration_time", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("endpoint", name="uq_viewer_push_subscriptions_endpoint"),
    )

    op.create_table(
        "viewer_notification_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("message_id", name="uq_viewer_notification_outbox_message_id"),
    )
    op.create_index("ix_viewer_notification_outbox_conversation_id", "viewer_notification_outbox", ["conversation_id"], unique=False)
    op.create_index("ix_viewer_notification_outbox_status", "viewer_notification_outbox", ["status"], unique=False)
    op.create_index("ix_viewer_notification_outbox_available_at", "viewer_notification_outbox", ["available_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_viewer_notification_outbox_available_at", table_name="viewer_notification_outbox")
    op.drop_index("ix_viewer_notification_outbox_status", table_name="viewer_notification_outbox")
    op.drop_index("ix_viewer_notification_outbox_conversation_id", table_name="viewer_notification_outbox")
    op.drop_table("viewer_notification_outbox")
    op.drop_table("viewer_push_subscriptions")
