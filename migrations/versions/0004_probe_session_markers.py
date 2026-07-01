"""add explicit probe/test session markers

Revision ID: 0004_probe_session_markers
Revises: 0003_vk_transport_state
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_probe_session_markers"
down_revision = "0003_vk_transport_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("is_test", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("conversations", sa.Column("source", sa.String(length=64), nullable=True))
    op.add_column("conversations", sa.Column("session_type", sa.String(length=64), nullable=True))
    op.add_column("conversations", sa.Column("scenario_name", sa.String(length=255), nullable=True))
    op.add_column("conversations", sa.Column("requested_by", sa.String(length=255), nullable=True))
    op.create_index("ix_conversations_is_test", "conversations", ["is_test"], unique=False)
    op.create_index("ix_conversations_source", "conversations", ["source"], unique=False)
    op.create_index("ix_conversations_session_type", "conversations", ["session_type"], unique=False)

    op.add_column("support_cases", sa.Column("is_test", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("support_cases", sa.Column("source", sa.String(length=64), nullable=True))
    op.add_column("support_cases", sa.Column("session_type", sa.String(length=64), nullable=True))
    op.add_column("support_cases", sa.Column("scenario_name", sa.String(length=255), nullable=True))
    op.add_column("support_cases", sa.Column("requested_by", sa.String(length=255), nullable=True))
    op.create_index("ix_support_cases_is_test", "support_cases", ["is_test"], unique=False)
    op.create_index("ix_support_cases_source", "support_cases", ["source"], unique=False)
    op.create_index("ix_support_cases_session_type", "support_cases", ["session_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_support_cases_session_type", table_name="support_cases")
    op.drop_index("ix_support_cases_source", table_name="support_cases")
    op.drop_index("ix_support_cases_is_test", table_name="support_cases")
    op.drop_column("support_cases", "requested_by")
    op.drop_column("support_cases", "scenario_name")
    op.drop_column("support_cases", "session_type")
    op.drop_column("support_cases", "source")
    op.drop_column("support_cases", "is_test")

    op.drop_index("ix_conversations_session_type", table_name="conversations")
    op.drop_index("ix_conversations_source", table_name="conversations")
    op.drop_index("ix_conversations_is_test", table_name="conversations")
    op.drop_column("conversations", "requested_by")
    op.drop_column("conversations", "scenario_name")
    op.drop_column("conversations", "session_type")
    op.drop_column("conversations", "source")
    op.drop_column("conversations", "is_test")
