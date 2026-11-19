"""add third basic reference to clients

BR-CLI-005 (2026-11-19). La entidad vuelve a pedir tres referencias, pero
las tres con el mismo formato básico (nombre de la persona, celular,
relación): la 1 y la 2 reutilizan las columnas personal_reference_1_*/2_*
que ya existían, y esta migración agrega la 3.

Nullable y **sin backfill**: la obligatoriedad vive en client_service.py, y
los clientes ya registrados no tienen una tercera referencia que se pueda
inventar -- se les pide al editarlos.

Revision ID: a4c2e8f19d37
Revises: b3f81c6d9a20
Create Date: 2026-11-19 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a4c2e8f19d37"
down_revision: Union[str, None] = "b3f81c6d9a20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "clients", sa.Column("personal_reference_3_name", sa.String(), nullable=True)
    )
    op.add_column(
        "clients",
        sa.Column("personal_reference_3_relationship", sa.String(), nullable=True),
    )
    op.add_column(
        "clients", sa.Column("personal_reference_3_phone", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("clients", "personal_reference_3_phone")
    op.drop_column("clients", "personal_reference_3_relationship")
    op.drop_column("clients", "personal_reference_3_name")
