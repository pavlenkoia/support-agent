"""add durable VK turns

Revision ID: 0007_vk_turns
Revises: 0006_transport_retries
Create Date: 2026-09-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_vk_turns"
down_revision = "0006_transport_retries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vk_turns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("support_cases.id"), nullable=True),
        sa.Column("first_event_id", sa.Integer(), sa.ForeignKey("transport_events.id"), nullable=False, unique=True),
        sa.Column("last_event_id", sa.Integer(), sa.ForeignKey("transport_events.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claim_token", sa.String(length=64), nullable=True, unique=True),
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_vk_turns_conversation_id", "vk_turns", ["conversation_id"], unique=False)
    op.create_index("ix_vk_turns_case_id", "vk_turns", ["case_id"], unique=False)
    op.create_index("ix_vk_turns_last_event_id", "vk_turns", ["last_event_id"], unique=False)
    op.create_index("ix_vk_turns_status", "vk_turns", ["status"], unique=False)
    op.create_index("ix_vk_turns_due_at", "vk_turns", ["due_at"], unique=False)
    op.create_index("ix_vk_turns_claim_until", "vk_turns", ["claim_until"], unique=False)


def downgrade() -> None:
    op.drop_table("vk_turns")
