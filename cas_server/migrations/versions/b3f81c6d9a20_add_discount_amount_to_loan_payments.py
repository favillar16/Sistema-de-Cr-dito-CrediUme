"""add discount_amount to loan_payments

BR-LOAN-018. Cancelación del préstamo en un solo pago con un descuento que
decide el operador. `amount` sigue significando "lo imputado al cronograma" --
en una cancelación, el saldo entero, que es lo que deja el préstamo en PAID --
y esta columna guarda la parte de ese saldo que no se cobró. Sin ella, el
arqueo, el reporte de período y el comprobante reimpreso no podrían distinguir
lo imputado de lo recibido.

Nullable y **sin backfill**, mismo criterio que late_fee_amount (a7e2d05c31f4):
ningún cobro anterior tuvo descuento, y se leen como vacío.

Revision ID: b3f81c6d9a20
Revises: a7e2d05c31f4
Create Date: 2026-09-30 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3f81c6d9a20"
down_revision: Union[str, None] = "a7e2d05c31f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "loan_payments",
        sa.Column("discount_amount", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    # Se pierde cuánto se descontó en cada cancelación. El dinero recibido
    # sigue en el movimiento de caja (si fue en efectivo) y el hecho en el
    # AuditLog, pero `amount` volvería a leerse como si se hubiera cobrado
    # entero.
    op.drop_column("loan_payments", "discount_amount")
