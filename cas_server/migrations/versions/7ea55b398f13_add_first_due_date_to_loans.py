"""add first_due_date to loans

Revision ID: 7ea55b398f13
Revises: dafc1159158f
Create Date: 2026-08-10 13:57:53.684097

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7ea55b398f13"
down_revision: Union[str, None] = "dafc1159158f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("loans", sa.Column("first_due_date", sa.Date(), nullable=True))
    op.execute(
        "UPDATE loans SET first_due_date = (created_at::date + INTERVAL '30 days') "
        "WHERE first_due_date IS NULL"
    )
    op.alter_column("loans", "first_due_date", nullable=False)


def downgrade() -> None:
    op.drop_column("loans", "first_due_date")
