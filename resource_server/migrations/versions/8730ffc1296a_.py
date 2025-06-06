"""empty message

Revision ID: 8730ffc1296a
Revises: 84df0c85f5f0
Create Date: 2025-06-01 11:40:56.232521

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '8730ffc1296a'
down_revision = '84df0c85f5f0'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('user_tickets',
    sa.Column('id', sa.BIGINT(), nullable=False),
    sa.Column('user_id', sa.BIGINT(), nullable=True),
    sa.Column('email', sa.VARCHAR(length=320), nullable=False),
    sa.Column('time_raised', postgresql.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('description', sa.VARCHAR(length=512), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('user_tickets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_tickets_email'), ['email'], unique=False)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('user_tickets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_tickets_email'))

    op.drop_table('user_tickets')
    # ### end Alembic commands ###
