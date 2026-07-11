"""add tool role to messages

Revision ID: a3f8c1d2e456
Revises: 9ce63be5b215
Create Date: 2026-07-11 07:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a3f8c1d2e456'
down_revision: Union[str, Sequence[str], None] = '9ce63be5b215'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Extend the role check constraint to accept tool messages."""
    op.drop_constraint("ck_messages_role", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_role",
        "messages",
        "role IN ('user', 'assistant', 'tool')",
    )


def downgrade() -> None:
    """Revert to the original two-role constraint."""
    op.drop_constraint("ck_messages_role", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_role",
        "messages",
        "role IN ('user', 'assistant')",
    )
