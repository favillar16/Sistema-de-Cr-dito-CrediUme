"""add late_fee_amount to loan_payments

BR-LOAN-017. La mora dejó de ser sólo texto del Pagaré y pasa a cobrarse: esta
columna guarda cuánto de un cobro fue recargo por atraso, separado de `amount`,
que sigue significando "lo imputado al cronograma". Mezclarlas haría que el
préstamo pasara a PAID antes de tiempo y que la imputación FIFO (BR-LOAN-009 a
011) contara el recargo como capital e interés.

Se **guarda** en vez de recalcularse, a diferencia del cronograma: la mora
devengada es función de la fecha y del saldo impago, así que una vez cubierta
la cuota el recálculo da cero y un comprobante reimpreso contradiría al
original. Es un hecho del cobro, como su responsable, no una derivación.

Nullable y **sin backfill**, mismo criterio que loan_payments.recorded_by_user_id
(f1c93a7b52de): los cobros anteriores a la regla no tuvieron mora, y ponerles
0,00 o inventarles una sería afirmar algo que nadie midió. Se leen como vacío.

Revision ID: a7e2d05c31f4
Revises: f1c93a7b52de
Create Date: 2026-09-22 11:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7e2d05c31f4"
down_revision: Union[str, None] = "f1c93a7b52de"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "loan_payments",
        sa.Column("late_fee_amount", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    # Se pierde el desglose de la mora cobrada. El dinero sigue registrado en
    # el movimiento de caja (que imputa cuota + mora) y el hecho en el
    # AuditLog, pero ya no se puede decir cuánto de cada cobro fue recargo.
    op.drop_column("loan_payments", "late_fee_amount")
