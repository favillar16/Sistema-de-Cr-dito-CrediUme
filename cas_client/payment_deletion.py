"""BR-LOAN-019: eliminar un cobro registrado por error.

Una sola definición del aviso de confirmación y del pedido de motivo para las
dos pantallas que muestran el historial de cobros (LoansView y CashView), por
el mismo motivo que payoff.py: son dos puertas a la misma regla, y la trampa
barata es actualizar una y olvidar la otra.

Lo que el operador tiene que saber antes de confirmar, y por eso lo dice el
aviso:
- qué cobro se borra (fecha, monto total, medio, cuotas);
- que en efectivo el ingreso sale de la caja si sigue abierta, y que con la
  caja ya cerrada el servidor lo va a rechazar;
- que los cobros posteriores se reimputan (FIFO) y sus comprobantes ya
  entregados pueden dejar de coincidir;
- que no se puede deshacer.
"""

from PySide6.QtWidgets import QInputDialog, QMessageBox, QWidget

from cas_client import documents
from cas_client.formatting import fecha_hora, gs

_METODOS = {"EFECTIVO": "Efectivo", "TRANSFERENCIA": "Transferencia"}


def confirmation_text(entry, total_installments: int, *, is_latest: bool) -> str:
    """Texto del diálogo de confirmación para el cobro `entry`
    (LoanPaymentEntry). Puro, para poder probarlo sin Qt."""
    medio = _METODOS.get(entry.payment_method, entry.payment_method or "—")
    cuotas = documents.cuotas_cubiertas_texto(
        entry.covered_installments, total_installments
    )
    partes = [
        "¿Eliminar este cobro registrado?",
        "",
        f"Fecha: {fecha_hora(entry.paid_at.ToDatetime())}",
        f"Total cobrado: {gs(entry.total_charged or entry.amount)}",
        f"Medio: {medio}",
        cuotas,
        "",
    ]
    if entry.payment_method == "EFECTIVO":
        partes.append(
            "Fue en efectivo: el ingreso se quita de la caja donde entró si esa "
            "caja sigue abierta. Si la caja ya se cerró, no se puede eliminar."
        )
    if not is_latest:
        partes.append(
            "No es el último cobro: los cobros posteriores pasarán a cubrir "
            "otras cuotas y sus comprobantes ya entregados pueden dejar de "
            "coincidir."
        )
    partes.append(
        "El préstamo recupera el saldo de este cobro. Esta acción no se puede "
        "deshacer: sólo queda el registro de auditoría."
    )
    return "\n".join(partes)


def ask_delete_reason(
    parent: QWidget, entry, total_installments: int, *, is_latest: bool
) -> str | None:
    """Confirmación + motivo. Devuelve el motivo, o None si el operador
    canceló en cualquiera de los dos pasos (sin llamar al servidor)."""
    confirm = QMessageBox.question(
        parent,
        "Eliminar cobro",
        confirmation_text(entry, total_installments, is_latest=is_latest),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if confirm != QMessageBox.StandardButton.Yes:
        return None
    motivo, ok = QInputDialog.getText(
        parent,
        "Motivo de la eliminación",
        "Indique por qué se elimina este cobro (queda auditado):",
    )
    if not ok:
        return None
    return motivo.strip() or ""


def success_message(response) -> str:
    """Toast tras eliminar: dice si salió plata de la caja."""
    if response.cash_amount_removed:
        return (
            f"Cobro eliminado. Se quitaron {gs(response.cash_amount_removed)} "
            "de la caja."
        )
    return "Cobro eliminado."
