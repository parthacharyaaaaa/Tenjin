"""renames admins.last_login column

Revision ID: d7ef65661fb0
Revises: 5f3ba2927c0b
Create Date: 2026-11-10 22:05:23.683535

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7ef65661fb0"
down_revision: Union[str, Sequence[str], None] = "5f3ba2927c0b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("admins", "last_login", new_column_name="last_auth_activity_at")


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("admins", "last_auth_activity_at", new_column_name="last_login")
