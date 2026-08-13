"""add created_by_user_id to loans

Revision ID: 22335ba7fdd7
Revises: 0887ff610957
Create Date: 2026-08-13 09:24:45.784164

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '22335ba7fdd7'
down_revision: Union[str, None] = '0887ff610957'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('loans', sa.Column('created_by_user_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_loans_created_by_user_id_users',
        'loans',
        'users',
        ['created_by_user_id'],
        ['id'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_loans_created_by_user_id_users', 'loans', type_='foreignkey'
    )
    op.drop_column('loans', 'created_by_user_id')
