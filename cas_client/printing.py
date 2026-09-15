"""Armado de la página para la impresora térmica de la caja (FTX FTXP 80W).

Separado de `documents.py` a propósito: ese módulo es HTML puro y no importa
Qt (por eso sus tests corren sin display), mientras que acá se toca
QPrinter/QPageLayout. Y separado de `cash_view.py` porque la geometría del
rollo no es una decisión de esa pantalla: es una característica del papel.

El punto no obvio de todo esto es la **altura de la página**. Un rollo
térmico no tiene páginas: el driver corta al final del trabajo, así que si se
le declara un alto fijo (los "80 x 297 mm" que trae por defecto el formulario
de estas impresoras) cada ticket se lleva casi 30 cm de papel en blanco. Por
eso la altura se **mide** a partir del contenido antes de imprimir, y la
página se arma exactamente de ese alto más la cola del corte.
"""

from PySide6.QtCore import QMarginsF, QSizeF
from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrinter

from cas_client.documents import TICKET_MARGIN_MM, TICKET_TAIL_MM, TICKET_WIDTH_MM

# QTextDocument dispone su contenido en píxeles lógicos a 96 dpi, no en
# puntos: un <p> de 8pt mide ~37 unidades de documento, no ~10. Mientras la
# medición y el tamaño de página que se le pasa después usen esta misma
# constante, un píxel de documento termina midiendo 1/96" sobre el papel y
# las tipografías salen en su tamaño real en puntos.
_PX_PER_MM = 96.0 / 25.4

# Alto de sobra para medir: la página "infinita" contra la cual se dispone el
# documento en la primera pasada, para que no se pagine y size() devuelva el
# alto real del contenido completo.
_ALTO_DE_MEDICION_PX = 100_000.0


def printable_width_mm() -> float:
    """Ancho útil del ticket: el papel menos los dos márgenes."""
    return TICKET_WIDTH_MM - 2 * TICKET_MARGIN_MM


def content_height_mm(html: str) -> float:
    """Alto que ocupa `html` dispuesto en el ancho útil del rollo.

    Primera de las dos pasadas: se dispone el documento contra una página de
    alto prácticamente infinito para que no se pagine, y se lee cuánto midió.
    """
    document = QTextDocument()
    document.setHtml(html)
    document.setPageSize(
        QSizeF(printable_width_mm() * _PX_PER_MM, _ALTO_DE_MEDICION_PX)
    )
    return document.size().height() / _PX_PER_MM


def apply_ticket_page(printer: QPrinter, html: str) -> None:
    """Deja `printer` configurado con una página del ancho del rollo y del
    alto exacto que necesita `html`.

    **Hay que llamarlo después del QPrintDialog, no antes**: al aceptar el
    diálogo Qt reemplaza el page layout por el de la impresora elegida, así
    que una página configurada antes se pierde y el ticket vuelve a salir en
    el formulario por defecto del driver.
    """
    alto = content_height_mm(html) + 2 * TICKET_MARGIN_MM + TICKET_TAIL_MM
    printer.setPageLayout(
        QPageLayout(
            QPageSize(
                QSizeF(TICKET_WIDTH_MM, alto),
                QPageSize.Unit.Millimeter,
                "Ticket80",
                # ExactMatch y no el default: sin esto Qt busca el tamaño
                # estándar más parecido y termina imprimiendo en A4.
                QPageSize.SizeMatchPolicy.ExactMatch,
            ),
            QPageLayout.Orientation.Portrait,
            QMarginsF(
                TICKET_MARGIN_MM,
                TICKET_MARGIN_MM,
                TICKET_MARGIN_MM,
                TICKET_MARGIN_MM,
            ),
            QPageLayout.Unit.Millimeter,
        )
    )


def render_ticket(printer: QPrinter, html: str) -> None:
    """Segunda pasada: arma el documento contra el área imprimible real que
    quedó en `printer` y lo manda a imprimir.

    Se usa el paintRect que reporta el printer (y no los milímetros nominales
    del rollo) porque el driver puede recortar el área imprimible por su
    cuenta; disponer el texto contra el ancho nominal en ese caso haría que la
    última columna saliera cortada.
    """
    apply_ticket_page(printer, html)
    area = printer.pageLayout().paintRect(QPageLayout.Unit.Millimeter)
    document = QTextDocument()
    document.setHtml(html)
    document.setPageSize(QSizeF(area.width() * _PX_PER_MM, area.height() * _PX_PER_MM))
    document.print_(printer)
