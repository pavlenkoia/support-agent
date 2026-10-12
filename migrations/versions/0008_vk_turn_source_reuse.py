"""Allow a superseded VK turn's unsent sources in its replacement turn.

Revision ID: 0008_vk_turn_source_reuse
Revises: 0007_vk_turns
"""

from alembic import op

revision = "0008_vk_turn_source_reuse"
down_revision = "0007_vk_turns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("vk_turns_first_event_id_key", "vk_turns", type_="unique")


def downgrade() -> None:
    raise NotImplementedError("Overlapping VK turn sources must be reconciled before restoring uniqueness")