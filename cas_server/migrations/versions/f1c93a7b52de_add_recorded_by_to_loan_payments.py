"""add recorded_by_user_id to loan_payments

BR-LOAN-016. Registra *quién* cobró cada cuota. Hasta acá el operador que
registraba el pago solo quedaba en dos lugares: el AuditLog
(PRESTAMO_PAGO_REGISTRADO, texto libre, no consultable por pago) y el papel
que se imprimía en ese mismo instante. Con el historial de cobros
consultable, el resto del personal tiene que poder ver quién tomó el dinero, y
un comprobante reimpreso tiene que poder nombrar al mismo cajero que el
original -- ninguna de las dos cosas se puede hacer desde el AuditLog.

Nullable y **sin backfill**, mismo criterio que loans.created_by_user_id
(22335ba7fdd7) y loans.disbursed_at (d4f7c2a91b08): los pagos anteriores no
tienen un responsable recuperable, y atribuírselos a alguien sería inventarlo.
Se leen como "" y el comprobante reimpreso lo dice en vez de mentir.

Revision ID: f1c93a7b52de
Revises: d4f7c2a91b08
Create Date: 2026-09-16 10:05:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1c93a7b52de"
down_revision: Union[str, None] = "d4f7c2a91b08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "loan_payments",
        sa.Column("recorded_by_user_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_loan_payments_recorded_by_user_id_users",
        "loan_payments",
        "users",
        ["recorded_by_user_id"],
        ["id"],
    )


def downgrade() -> None:
    # Se pierde el responsable de cada cobro; el AuditLog conserva el hecho
    # pero no de forma estructurada por pago.
    op.drop_constraint(
        "fk_loan_payments_recorded_by_user_id_users",
        "loan_payments",
        type_="foreignkey",
    )
    op.drop_column("loan_payments", "recorded_by_user_id")
