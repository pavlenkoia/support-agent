"""add vk transport state tables

Revision ID: 0003_vk_transport_state
Revises: 0002_persist_case_identity
Create Date: 2026-06-26
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_vk_transport_state"
down_revision = "0002_persist_case_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transport_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=True),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_external_id", sa.String(length=255), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="received"),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.UniqueConstraint("dedupe_key", name="uq_transport_events_dedupe_key"),
    )
    op.create_index("ix_transport_events_platform", "transport_events", ["platform"], unique=False)
    op.create_index("ix_transport_events_event_type", "transport_events", ["event_type"], unique=False)
    op.create_index("ix_transport_events_conversation_external_id", "transport_events", ["conversation_external_id"], unique=False)

    op.create_table(
        "outbound_transport_sends",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("support_cases.id"), nullable=True),
        sa.Column("peer_external_id", sa.String(length=255), nullable=False),
        sa.Column("random_id", sa.String(length=64), nullable=False),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("sent_by", sa.String(length=32), nullable=False, server_default="bot"),
        sa.Column("send_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("random_id", name="uq_outbound_transport_sends_random_id"),
    )
    op.create_index("ix_outbound_transport_sends_platform", "outbound_transport_sends", ["platform"], unique=False)
    op.create_index("ix_outbound_transport_sends_conversation_id", "outbound_transport_sends", ["conversation_id"], unique=False)
    op.create_index("ix_outbound_transport_sends_case_id", "outbound_transport_sends", ["case_id"], unique=False)
    op.create_index("ix_outbound_transport_sends_peer_external_id", "outbound_transport_sends", ["peer_external_id"], unique=False)
    op.create_index("ix_outbound_transport_sends_external_message_id", "outbound_transport_sends", ["external_message_id"], unique=False)
    op.create_index("ix_outbound_transport_sends_content_hash", "outbound_transport_sends", ["content_hash"], unique=False)
    op.create_index("ix_outbound_transport_sends_sent_at", "outbound_transport_sends", ["sent_at"], unique=False)

    op.create_table(
        "conversation_transport_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("last_inbound_external_message_id", sa.String(length=255), nullable=True),
        sa.Column("last_bot_reply_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_admin_reply_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("human_override_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("conversation_id", name="uq_conversation_transport_states_conversation_id"),
    )
    op.create_index("ix_conversation_transport_states_conversation_id", "conversation_transport_states", ["conversation_id"], unique=True)
    op.create_index("ix_conversation_transport_states_platform", "conversation_transport_states", ["platform"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_conversation_transport_states_platform", table_name="conversation_transport_states")
    op.drop_index("ix_conversation_transport_states_conversation_id", table_name="conversation_transport_states")
    op.drop_table("conversation_transport_states")

    op.drop_index("ix_outbound_transport_sends_sent_at", table_name="outbound_transport_sends")
    op.drop_index("ix_outbound_transport_sends_content_hash", table_name="outbound_transport_sends")
    op.drop_index("ix_outbound_transport_sends_external_message_id", table_name="outbound_transport_sends")
    op.drop_index("ix_outbound_transport_sends_peer_external_id", table_name="outbound_transport_sends")
    op.drop_index("ix_outbound_transport_sends_case_id", table_name="outbound_transport_sends")
    op.drop_index("ix_outbound_transport_sends_conversation_id", table_name="outbound_transport_sends")
    op.drop_index("ix_outbound_transport_sends_platform", table_name="outbound_transport_sends")
    op.drop_table("outbound_transport_sends")

    op.drop_index("ix_transport_events_conversation_external_id", table_name="transport_events")
    op.drop_index("ix_transport_events_event_type", table_name="transport_events")
    op.drop_index("ix_transport_events_platform", table_name="transport_events")
    op.drop_table("transport_events")
