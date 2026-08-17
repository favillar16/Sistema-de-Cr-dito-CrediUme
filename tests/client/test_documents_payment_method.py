"""BR-CAJA-004: cómo el Comprobante de Pago describe el medio de cobro.

Función pura sobre un stub de respuesta -- sin Qt, sin gRPC, sin base, igual
que el resto de tests/client/.
"""

from dataclasses import dataclass

from cas_client.documents import filas_medio_de_pago


@dataclass
class _PagoFalso:
    """Lo mínimo de loan_service_pb2.RecordPaymentResponse que mira
    filas_medio_de_pago()."""

    payment_method: str
    transfer_reference: str = ""


def test_cash_payment_shows_the_method_and_omits_the_reference():
    """En efectivo no hay número de transferencia: imprimir la fila vacía
    haría parecer que se perdió un dato."""
    filas = filas_medio_de_pago(_PagoFalso("EFECTIVO"))

    assert filas == [("Medio de pago", "Efectivo (caja)")]


def test_cash_payment_omits_the_reference_even_if_one_was_sent():
    """El servidor ignora transfer_reference en efectivo (lo guarda en NULL),
    así que el comprobante tampoco debe mostrarlo aunque venga en la
    respuesta."""
    filas = filas_medio_de_pago(_PagoFalso("EFECTIVO", "TRF-999"))

    assert all(concepto != "Referencia de transferencia" for concepto, _ in filas)


def test_transfer_payment_keeps_showing_the_reference():
    filas = filas_medio_de_pago(_PagoFalso("TRANSFERENCIA", "TRF-001"))

    assert filas == [
        ("Medio de pago", "Transferencia / descuento"),
        ("Referencia de transferencia", "TRF-001"),
    ]


def test_an_empty_method_reads_as_transfer():
    """Compatibilidad con un servidor anterior a BR-CAJA-004, donde todo pago
    era por transferencia -- mismo valor por defecto que aplica el servidor."""
    filas = filas_medio_de_pago(_PagoFalso("", "TRF-002"))

    assert filas[0] == ("Medio de pago", "Transferencia / descuento")
    assert ("Referencia de transferencia", "TRF-002") in filas


def test_an_unknown_method_is_printed_verbatim():
    """No debería ocurrir (el servidor solo emite dos valores), pero un
    comprobante es un documento que se entrega: mejor imprimir el valor crudo
    que reventar al renderizarlo."""
    filas = filas_medio_de_pago(_PagoFalso("CHEQUE"))

    assert filas[0] == ("Medio de pago", "CHEQUE")
