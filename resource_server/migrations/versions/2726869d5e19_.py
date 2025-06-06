"""empty message

Revision ID: 2726869d5e19
Revises: 63989a870f83
Create Date: 2025-03-30 09:27:23.046232

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2726869d5e19'
down_revision = '63989a870f83'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('animes', schema=None) as batch_op:
        batch_op.alter_column('mal_ranking',
               existing_type=sa.INTEGER(),
               nullable=True)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('animes', schema=None) as batch_op:
        batch_op.alter_column('mal_ranking',
               existing_type=sa.INTEGER(),
               nullable=False)

    # ### end Alembic commands ###
