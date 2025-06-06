"""empty message

Revision ID: fc411ecd643e
Revises: a1d8f5a0c609
Create Date: 2025-05-16 13:05:42.949126

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'fc411ecd643e'
down_revision = 'a1d8f5a0c609'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('post_reports', schema=None) as batch_op:
        REPORT_TAGS = sa.Enum('spam', 'harassment', 'hate', 'violence', 'other', name='REPORT_TAGS')
        REPORT_TAGS.create(op.get_bind())
        batch_op.add_column(sa.Column('report_time', postgresql.TIMESTAMP(), nullable=True))
        batch_op.add_column(sa.Column('report_description', sa.VARCHAR(length=256), nullable=False))
        batch_op.add_column(sa.Column('report_tag', REPORT_TAGS, nullable=False))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('post_reports', schema=None) as batch_op:
        batch_op.drop_column('report_tag')
        batch_op.drop_column('report_description')
        batch_op.drop_column('report_time')

    # ### end Alembic commands ###
