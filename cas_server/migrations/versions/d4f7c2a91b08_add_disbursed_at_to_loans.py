"""add disbursed_at to loans

Registra *cuándo* se desembolsó un préstamo. Hasta acá el sistema sabía que un
préstamo estaba desembolsado (status ACTIVE) pero no en qué fecha, así que el
reporte de cierre de período (BR-DASH-002) no podía totalizar el dinero que
efectivamente salió de la entidad en el mes -- solo lo solicitado y lo
aprobado, que son decisiones, no salidas de caja.

Nullable y **sin backfill**, mismo criterio que created_by_user_id
(22335ba7fdd7): los préstamos ya desembolsados no tienen una fecha real que
recuperar, y ponerles `approved_at` o `created_at` inventaría un desembolso en
un período al que puede no pertenecer. Quedan con NULL, es decir fuera de todo
rango del reporte; la cifra histórica total sigue estando en el panel
("Cartera desembolsada"), que no depende de esta columna.

Revision ID: d4f7c2a91b08
Revises: c7a1d4e5f6b2
Create Date: 2026-09-14 21:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4f7c2a91b08"
down_revision: Union[str, None] = "c7a1d4e5f6b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "loans",
        sa.Column("disbursed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Se pierden las fechas de desembolso registradas; no hay de dónde
    # recomponerlas salvo el AuditLog (PRESTAMO_DESEMBOLSADO), que no es un
    # origen estructurado.
    op.drop_column("loans", "disbursed_at")
