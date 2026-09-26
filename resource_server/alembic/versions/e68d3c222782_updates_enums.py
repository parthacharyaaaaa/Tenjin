"""updates enums

Revision ID: e68d3c222782
Revises: f00622df4456
Create Date: 2026-09-27 01:00:26.793064

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e68d3c222782"
down_revision: Union[str, Sequence[str], None] = "f00622df4456"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Recreate report and administrator enums with uppercase values."""
    bind = op.get_bind()

    op.alter_column("forum_admins", "role", server_default=None)
    op.alter_column(
        "forum_admins",
        "role",
        existing_type=postgresql.ENUM(name="ADMIN_ROLES"),
        type_=sa.String(),
        postgresql_using="role::text",
    )
    for table_name in ("post_reports", "comment_reports"):
        op.alter_column(
            table_name,
            "report_tag",
            existing_type=postgresql.ENUM(name="REPORT_TAGS"),
            type_=sa.String(),
            postgresql_using="report_tag::text",
        )

    postgresql.ENUM(name="ADMIN_ROLES").drop(bind, checkfirst=True)
    postgresql.ENUM(name="REPORT_TAGS").drop(bind, checkfirst=True)

    op.execute(sa.text("UPDATE forum_admins SET role = upper(role)"))
    op.execute(sa.text("UPDATE post_reports SET report_tag = upper(report_tag)"))
    op.execute(sa.text("UPDATE comment_reports SET report_tag = upper(report_tag)"))

    admin_roles = postgresql.ENUM("ADMIN", "SUPER", "OWNER", name="ADMIN_ROLES")
    report_tags = postgresql.ENUM(
        "SPAM", "HARASSMENT", "HATE", "VIOLENCE", "OTHER", name="REPORT_TAGS"
    )
    admin_roles.create(bind, checkfirst=True)
    report_tags.create(bind, checkfirst=True)

    op.alter_column(
        "forum_admins",
        "role",
        existing_type=sa.String(),
        type_=admin_roles,
        postgresql_using='role::text::"ADMIN_ROLES"',
        server_default=sa.text("'ADMIN'::\"ADMIN_ROLES\""),
    )
    for table_name in ("post_reports", "comment_reports"):
        op.alter_column(
            table_name,
            "report_tag",
            existing_type=sa.String(),
            type_=report_tags,
            postgresql_using='report_tag::text::"REPORT_TAGS"',
        )


def downgrade() -> None:
    """Restore the lowercase enum values from the preceding revision."""
    bind = op.get_bind()

    op.alter_column("forum_admins", "role", server_default=None)
    op.alter_column(
        "forum_admins",
        "role",
        existing_type=postgresql.ENUM(name="ADMIN_ROLES"),
        type_=sa.String(),
        postgresql_using="role::text",
    )
    for table_name in ("post_reports", "comment_reports"):
        op.alter_column(
            table_name,
            "report_tag",
            existing_type=postgresql.ENUM(name="REPORT_TAGS"),
            type_=sa.String(),
            postgresql_using="report_tag::text",
        )

    postgresql.ENUM(name="ADMIN_ROLES").drop(bind, checkfirst=True)
    postgresql.ENUM(name="REPORT_TAGS").drop(bind, checkfirst=True)

    op.execute(sa.text("UPDATE forum_admins SET role = lower(role)"))
    op.execute(sa.text("UPDATE post_reports SET report_tag = lower(report_tag)"))
    op.execute(sa.text("UPDATE comment_reports SET report_tag = lower(report_tag)"))

    admin_roles = postgresql.ENUM("admin", "super", "owner", name="ADMIN_ROLES")
    report_tags = postgresql.ENUM(
        "spam", "harassment", "hate", "violence", "other", name="REPORT_TAGS"
    )
    admin_roles.create(bind, checkfirst=True)
    report_tags.create(bind, checkfirst=True)

    op.alter_column(
        "forum_admins",
        "role",
        existing_type=sa.String(),
        type_=admin_roles,
        postgresql_using='role::text::"ADMIN_ROLES"',
        server_default=sa.text("'admin'::\"ADMIN_ROLES\""),
    )
    for table_name in ("post_reports", "comment_reports"):
        op.alter_column(
            table_name,
            "report_tag",
            existing_type=sa.String(),
            type_=report_tags,
            postgresql_using='report_tag::text::"REPORT_TAGS"',
        )
