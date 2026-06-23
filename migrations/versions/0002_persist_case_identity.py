"""add persisted user/channel identity and workflow payload

Revision ID: 0002_persist_case_identity
Revises: 0001_initial_schema
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_persist_case_identity"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(length=255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_users_external_id", "users", ["external_id"], unique=True)

    op.create_table(
        "channel_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("external_user_id", sa.String(length=255), nullable=False),
        sa.Column("external_chat_id", sa.String(length=255), nullable=False),
        sa.UniqueConstraint("channel", "external_user_id", "external_chat_id", name="uq_channel_account_identity"),
    )
    op.create_index("ix_channel_accounts_user_id", "channel_accounts", ["user_id"], unique=False)
    op.create_index("ix_support_cases_conversation_id", "support_cases", ["conversation_id"], unique=False)
    op.create_index("ix_messages_case_id", "messages", ["case_id"], unique=False)
    op.create_index("ix_workflow_events_case_id", "workflow_events", ["case_id"], unique=False)
    op.add_column("workflow_events", sa.Column("payload", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("workflow_events", "payload")
    op.drop_index("ix_workflow_events_case_id", table_name="workflow_events")
    op.drop_index("ix_messages_case_id", table_name="messages")
    op.drop_index("ix_support_cases_conversation_id", table_name="support_cases")
    op.drop_index("ix_channel_accounts_user_id", table_name="channel_accounts")
    op.drop_table("channel_accounts")
    op.drop_index("ix_users_external_id", table_name="users")
    op.drop_table("users")
