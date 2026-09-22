"""Tamaño de hoja de los documentos A4 (PDF e impresión).

Separado de `printing.py` a propósito: ese módulo habla con la impresora
térmica vía `win32print` y no se puede importar donde no haya pywin32; acá
sólo hay Qt, que es lo que usan los documentos A4. Y separado de
`documents.py` porque ese módulo arma HTML sin tocar Qt (sus tests corren sin
Qt instalado) y esto es puro `QPrinter`.

**Por qué existe.** Hasta 2026-09-22 ningún documento fijaba su hoja: el PDF y
la impresión heredaban el tamaño de papel de la impresora predeterminada de
Windows, y el `.docx` salía en Carta (el default de la plantilla de
python-docx). La Ficha de Cliente está diseñada para A4 y en Carta hay 17,6 mm
menos de alto, así que en una PC con la impresora en Carta se pasaba a una
segunda hoja -- sin que nada en el código lo dijera y sin un test que lo
detectara. Declarar la hoja acá saca al documento de esa dependencia.

**Por qué A4 y no Oficio.** La entidad usa las dos. A4 (210 × 297) es más
chica que Oficio (216 × 330) en ancho y en alto, así que un documento que
entra en A4 entra también en una hoja de Oficio, con papel de sobra abajo. Al
revés no: diseñar para Oficio garantizaría el desborde en A4.
"""

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize

# 8 mm por lado: entra dentro del área imprimible de cualquier láser/inkjet
# doméstico (que suele reservar entre 4 y 6 mm) sin que el texto toque el
# borde del papel. No es un número estético -- la Ficha de Cliente entra en
# una hoja por poco, y cada milímetro de margen le saca alto útil y además le
# angosta la línea, lo que hace que los campos largos (dirección, origen de
# fondos) corten en más renglones. Bajarlo más empieza a arriesgar recorte en
# impresoras con bordes no imprimibles grandes.
_MARGEN_MM = 8.0


def aplicar_hoja(printer, *, apaisado: bool = False) -> None:
    """Fija A4 y los márgenes del documento en un `QPrinter`.

    `apaisado` es para el reporte de estado de pagos (8 columnas), el único
    documento de la app que se imprime horizontal.

    Ojo con el orden cuando hay un `QPrintDialog` de por medio: aceptar el
    diálogo reemplaza el layout de página por el de la impresora elegida, así
    que esto tiene que correr **después** de `dialog.exec()`, no antes. Es la
    misma trampa que documentaba el viejo `apply_ticket_page()` del ticket
    térmico.
    """
    orientacion = (
        QPageLayout.Orientation.Landscape
        if apaisado
        else QPageLayout.Orientation.Portrait
    )
    printer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            orientacion,
            QMarginsF(_MARGEN_MM, _MARGEN_MM, _MARGEN_MM, _MARGEN_MM),
            QPageLayout.Unit.Millimeter,
        )
    )
