"""Entrega del ticket de 80 mm al puerto físico de la impresora térmica.

Separado de `escpos.py` a propósito: ese módulo arma los bytes ESC/POS del
ticket sin tocar Windows (por eso sus tests corren sin la impresora real,
igual que `documents.py` corre sin Qt), mientras que acá se abre el spool de
Windows con `win32print` y se empujan esos bytes en crudo. Y separado de
`cash_view.py` porque cuál impresora usar y cómo hablarle es una
característica de la caja, no de la pantalla.

**Por qué en crudo y no con QPrinter/QTextDocument (Qt).** La primera versión
de esto armaba el ticket como HTML y lo imprimía con el pipeline gráfico de
Qt, midiendo el contenido para declarar una página de 80 mm de ancho por el
alto exacto del ticket (ver el historial de este archivo). Contra la
impresora física de la caja ("Printer POS-80", USB\\VID_1FC9&PID_2016) eso
nunca funcionó: `scripts/diagnostico_impresora_ticket.py` mostró que el
driver instalado ("Generic / Text Only") y los dos genéricos de Windows que
se probaron en su reemplazo ("Microsoft Virtual Print Class Driver",
"Universal Print Class Driver") ignoran cualquier tamaño de página
personalizado y fuerzan A4 sin avisar -- ninguno de los tres es un driver
gráfico real, así que no hay tamaño de página que Qt les pueda pedir que
efectivamente se respete.

La salida es mandar los bytes ESC/POS directo al puerto con el datatype
"RAW" de la API de impresión de Windows (`win32print`): ese modo no pasa por
el renderer gráfico del driver en absoluto, así que a la impresora le da
igual qué driver tenga instalado -- es historia de por qué "Generic / Text
Only" venía siendo la opción que menos rompía las cosas hasta ahora, aunque
tampoco imprimiera el ticket con el formato correcto.
"""

import pywintypes
import win32print

from cas_client import config
from cas_client.escpos import ticket_cobro_escpos


class PrinterNotFoundError(Exception):
    """No hay impresora a la cual mandar el ticket.

    Ni `TICKET_PRINTER_NAME` apunta a una impresora instalada, ni (si no se
    configuró nada) Windows tiene una impresora predeterminada. Se levanta en
    vez de imprimir a ciegas en lo que sea que Windows elija -- un ticket que
    sale en la impresora equivocada, o que no sale porque no hay ninguna, es
    peor que uno que avisa por qué no se imprimió."""


class PrinterError(Exception):
    """La API de impresión de Windows (OpenPrinter/StartDocPrinter/
    WritePrinter) devolvió un error al mandar el trabajo. Envuelve el
    `pywintypes.error` original -- ver su mensaje para el detalle."""


def _printer_name() -> str:
    """Nombre de la impresora a la que se manda el ticket.

    `TICKET_PRINTER_NAME` (cas_client/.env) manda; vacío cae a la
    predeterminada de Windows. GetDefaultPrinter() levanta RuntimeError si no
    hay ninguna -- se traduce a PrinterNotFoundError, el mismo tipo que el
    caso de un nombre configurado que no existe.
    """
    nombre = config.TICKET_PRINTER_NAME
    if nombre:
        return nombre
    try:
        return win32print.GetDefaultPrinter()
    except RuntimeError as exc:
        raise PrinterNotFoundError(
            "No hay ninguna impresora configurada (TICKET_PRINTER_NAME) ni "
            "una impresora predeterminada en Windows."
        ) from exc


def _enviar_raw(printer_name: str, datos: bytes) -> None:
    """Abre `printer_name` y le manda `datos` como un trabajo RAW -- sin
    reinterpretarlos, sea cual sea el driver instalado."""
    try:
        handle = win32print.OpenPrinter(printer_name)
    except pywintypes.error as exc:
        instaladas = ", ".join(
            nombre
            for _flags, _srv, nombre, _cmt in win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL
            )
        )
        raise PrinterNotFoundError(
            f'No se encontró la impresora "{printer_name}" (TICKET_PRINTER_NAME '
            f"en cas_client/.env). Impresoras instaladas: {instaladas or 'ninguna'}."
        ) from exc
    try:
        job = win32print.StartDocPrinter(handle, 1, ("Ticket de cobro", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            try:
                win32print.WritePrinter(handle, datos)
            finally:
                win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
        del job
    except pywintypes.error as exc:
        raise PrinterError(
            f'La impresora "{printer_name}" rechazó el ticket: {exc.strerror or exc}.'
        ) from exc
    finally:
        win32print.ClosePrinter(handle)


def print_ticket(loan, client, payment) -> None:
    """Arma el ticket (escpos.ticket_cobro_escpos) y lo manda directo a la
    impresora térmica configurada, sin diálogo: la PC de caja tiene una sola
    impresora física y elegirla en cada cobro no aporta nada.

    Punto de entrada único para las vistas -- solo necesitan atrapar
    PrinterNotFoundError y PrinterError.
    """
    nombre = _printer_name()
    datos = ticket_cobro_escpos(loan, client, payment)
    _enviar_raw(nombre, datos)
