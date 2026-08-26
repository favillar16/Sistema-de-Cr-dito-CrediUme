"""drop extended profile fields from clients

Revierte la migración 910ca69c913b: el perfil extendido (BR-CLI-007) se
eliminó del sistema por decisión de producto -- ninguno de los 7 campos era
obligatorio ni participaba de una regla de negocio, y el formulario de alta
ya era el más largo de la aplicación.

Los datos que hubiera en esas columnas se pierden al aplicar esta migración:
el downgrade recrea las columnas vacías, no su contenido.

Revision ID: c7a1d4e5f6b2
Revises: b3b3955a3f99
Create Date: 2026-08-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7a1d4e5f6b2"
down_revision: Union[str, None] = "b3b3955a3f99"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("clients", "economic_sector")
    op.drop_column("clients", "risk_rating")
    op.drop_column("clients", "neighborhood")
    op.drop_column("clients", "occupation")
    op.drop_column("clients", "education_level")
    op.drop_column("clients", "marital_status")
    op.drop_column("clients", "national_id_expiry_date")


def downgrade() -> None:
    op.add_column(
        "clients", sa.Column("national_id_expiry_date", sa.Date(), nullable=True)
    )
    op.add_column("clients", sa.Column("marital_status", sa.String(), nullable=True))
    op.add_column("clients", sa.Column("education_level", sa.String(), nullable=True))
    op.add_column("clients", sa.Column("occupation", sa.String(), nullable=True))
    op.add_column("clients", sa.Column("neighborhood", sa.String(), nullable=True))
    op.add_column("clients", sa.Column("risk_rating", sa.String(), nullable=True))
    op.add_column("clients", sa.Column("economic_sector", sa.String(), nullable=True))
