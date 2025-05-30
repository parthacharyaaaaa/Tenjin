"""empty message

Revision ID: 63989a870f83
Revises: 
Create Date: 2025-03-30 09:19:52.683992

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '63989a870f83'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('stream_links', schema=None) as batch_op:
        batch_op.alter_column('website',
               existing_type=sa.VARCHAR(length=64),
               type_=sa.VARCHAR(length=128),
               existing_nullable=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rtfb', sa.BOOLEAN(), server_default=sa.text('false'), nullable=False))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('rtfb')

    with op.batch_alter_table('stream_links', schema=None) as batch_op:
        batch_op.alter_column('website',
               existing_type=sa.VARCHAR(length=128),
               type_=sa.VARCHAR(length=64),
               existing_nullable=False)

    # ### end Alembic commands ###
