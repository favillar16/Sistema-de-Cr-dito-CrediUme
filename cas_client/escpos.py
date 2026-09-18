"""Ticket de cobro de 80 mm, como comandos ESC/POS crudos.

Reemplaza el camino que armaba el ticket como HTML y lo imprimía con
QTextDocument/QPrinter (Qt), confirmado inservible en la impresora real de la
caja el 2026-09-18 con `scripts/diagnostico_impresora_ticket.py`: tanto el
driver "Generic / Text Only" como los genéricos de Windows "Microsoft Virtual
Print Class Driver" y "Universal Print Class Driver" ignoran cualquier
tamaño de página personalizado que Qt les pida y fuerzan A4 -- ninguno de
los tres es un driver gráfico real. `cas_client/printing.py` es lo que
manda estos bytes al puerto de la impresora sin pasar por el sistema de
páginas de Windows en absoluto (ver ese módulo).

Puramente funcional, igual que `documents.py` (que sigue siendo la plantilla
HTML del ticket, todavía usada -- ver su docstring): sin Qt ni win32print acá,
para que sus tests corran sin Windows real. Reutiliza los helpers de
`documents.py` (numero_ticket, cuotas_cubiertas_texto, filas_medio_de_pago,
responsable, es_reimpresion, las constantes _COMPANY_*) en vez de restatearlos
-- mismo criterio que `documents_docx.py` (ver CLAUDE.md): el ticket que se
imprime y el que describen los tests de `documents.py` no pueden hablar del
mismo cobro con datos distintos.

Font A a 80 mm / 203 dpi da 48 columnas en la inmensa mayoría de los clones
ESC/POS (compatibles Epson TM-T88) -- _COLUMNAS. Si el ticket real sale con
el margen derecho desperdiciado, o las líneas dan la vuelta antes de llegar
al borde, es el primer número a ajustar tras ver el papel real.
"""

from cas_client.documents import (
    _COMPANY_ADDRESS,
    _COMPANY_NAME,
    _COMPANY_PHONE,
    _COMPANY_RUC,
    cuotas_cubiertas_texto,
    es_reimpresion,
    filas_medio_de_pago,
    numero_ticket,
    responsable,
)
from cas_client.formatting import fecha_hora, gs

_COLUMNAS = 48

_ESC = b"\x1b"
_GS = b"\x1d"

_INICIALIZAR = _ESC + b"@"
# Codepage 0 = PC437 (western Europe/US), la misma con la que se codifica el
# texto acá abajo -- sin esto se depende de lo que el driver haya dejado
# seleccionado de una impresión anterior.
_CODIFICACION_CP437 = _ESC + b"t\x00"
_ALINEAR_IZQ = _ESC + b"a\x00"
_ALINEAR_CENTRO = _ESC + b"a\x01"
_NEGRITA_ON = _ESC + b"E\x01"
_NEGRITA_OFF = _ESC + b"E\x00"
_DOBLE_ALTO_ANCHO = _GS + b"!\x11"
_TAMANO_NORMAL = _GS + b"!\x00"
# Corte parcial (deja una pestaña de papel sin cortar del todo) en vez de
# total: es el más soportado entre los clones -- uno que no entiende GS V en
# absoluto simplemente no corta, mientras que pedir el modo que no tiene
# puede quedarse esperando el byte de confirmación.
_CORTE_PARCIAL = _GS + b"V\x01"
_AVANZAR_3_LINEAS = _ESC + b"d\x03"

# CP437 no tiene raya (—) ni guion medio tipográfico (–): sin esto, la
# dirección de la entidad (que sí los usa, para el A4/HTML donde QTextDocument
# admite unicode completo) sale en el papel como un "?" de reemplazo en vez de
# un guion -- peor que degradar a ASCII a propósito.
_SANEAMIENTO = {
    "—": "-",
    "–": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
}


def _sanear(texto: str) -> str:
    for buscado, reemplazo in _SANEAMIENTO.items():
        texto = texto.replace(buscado, reemplazo)
    return texto


def _texto(linea: str) -> bytes:
    return _sanear(linea).encode("cp437", errors="replace") + b"\n"


def _centrado(linea: str) -> bytes:
    return _ALINEAR_CENTRO + _texto(linea) + _ALINEAR_IZQ


def _centrado_negrita(linea: str) -> bytes:
    return _ALINEAR_CENTRO + _NEGRITA_ON + _texto(linea) + _NEGRITA_OFF + _ALINEAR_IZQ


def _separador() -> bytes:
    return _texto("-" * _COLUMNAS)


def _fila(etiqueta: str, valor: str, *, negrita: bool = False) -> bytes:
    """Etiqueta a la izquierda, valor a la derecha, en _COLUMNAS caracteres.

    Funciona sin tabla porque Font A es monoespaciada -- a diferencia del
    ticket HTML/QTextDocument, donde la fuente es proporcional y esto
    necesita una tabla real (ver `documents._ticket_filas`). Si no entra en
    una sola línea, la etiqueta va arriba y el valor abajo alineado a la
    derecha, en vez de cortarlo.
    """
    disponible = _COLUMNAS - len(etiqueta) - 1
    if disponible < len(valor):
        cuerpo = etiqueta + "\n" + valor.rjust(_COLUMNAS)
    else:
        cuerpo = etiqueta + valor.rjust(disponible + 1)
    if not negrita:
        return _texto(cuerpo)
    return _NEGRITA_ON + _texto(cuerpo) + _NEGRITA_OFF


def ticket_cobro_escpos(loan, client, payment) -> bytes:
    """Bytes ESC/POS del ticket de cobro de 80 mm, listos para mandar en
    crudo (datatype "RAW") a la impresora térmica de la caja.

    Documenta el mismo hecho que `documents.ticket_cobro_html` (BR-LOAN-011):
    mismo origen de datos (la respuesta de RecordPayment, nunca lo que el
    cliente creyó haber enviado) y los mismos helpers compartidos.

    loan: loan_service_pb2.GetLoanByIdResponse
    client: client_service_pb2.GetClientByIdResponse
    payment: loan_service_pb2.RecordPaymentResponse (o documents.CobroHistorico)
    """
    cuotas = cuotas_cubiertas_texto(
        payment.covered_installments, payment.total_installments
    )
    cajero = responsable(payment.recorded_by_name, payment.recorded_by_national_id)
    # fecha_hora() y no un strftime propio: paid_at viaja como un naive en
    # UTC, y el ticket tiene que decir la hora local a la que se pagó.
    fecha_pago = fecha_hora(payment.paid_at.ToDatetime())

    partes = [_INICIALIZAR, _CODIFICACION_CP437]

    partes.append(_centrado_negrita(_COMPANY_NAME))
    partes.append(_centrado(f"RUC: {_COMPANY_RUC}"))
    partes.append(_centrado(_COMPANY_ADDRESS))
    partes.append(_centrado(f"Cel: {_COMPANY_PHONE}"))
    partes.append(_separador())

    partes.append(_centrado_negrita("RECIBO DE COBRO"))
    partes.append(_centrado(f"N° {numero_ticket(loan, payment)}"))
    if es_reimpresion(payment):
        partes.append(_centrado_negrita("** REIMPRESIÓN **"))
    partes.append(_separador())

    partes.append(_fila("Cliente", f"{client.first_name} {client.last_name}"))
    partes.append(_fila("C.I.", client.national_id))
    partes.append(_fila("Teléfono", client.phone_number))
    partes.append(_separador())

    partes.append(_fila("Préstamo N°", loan.id[:8].upper()))
    partes.append(_fila("Cuotas abonadas", cuotas))
    partes.append(_fila("Fecha y hora", fecha_pago))
    partes.append(_separador())

    partes.append(_DOBLE_ALTO_ANCHO + _NEGRITA_ON)
    partes.append(_texto("MONTO ABONADO"))
    partes.append(_centrado(gs(payment.amount_paid)))
    partes.append(_NEGRITA_OFF + _TAMANO_NORMAL)
    partes.append(_separador())

    for concepto, valor in filas_medio_de_pago(payment):
        partes.append(_fila(concepto, valor))
    partes.append(_fila("Total pagado del préstamo", gs(payment.total_paid)))
    partes.append(_fila("Saldo restante", gs(payment.remaining_balance), negrita=True))
    if payment.status == "PAID":
        partes.append(_centrado_negrita("PRÉSTAMO TOTALMENTE CANCELADO"))
    partes.append(_separador())

    partes.append(_fila("Cajero/a", cajero))
    partes.append(_texto(""))
    partes.append(_texto(""))
    partes.append(_separador())
    partes.append(_centrado_negrita("SELLO Y FIRMA"))

    partes.append(_texto(""))
    partes.append(_centrado_negrita("¡Gracias por su pago!"))
    partes.append(_centrado("Conserve este comprobante como constancia"))
    partes.append(_centrado("de la operación."))
    partes.append(_centrado(f"{_COMPANY_NAME} - Cel: {_COMPANY_PHONE}"))

    partes.append(_AVANZAR_3_LINEAS)
    partes.append(_CORTE_PARCIAL)
    return b"".join(partes)
