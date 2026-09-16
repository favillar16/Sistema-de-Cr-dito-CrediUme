"""Los documentos tienen que salir del mismo tamaño en el papel que en pantalla.

Defecto que motivó estos tests: `QTextDocument` resuelve un `font-size` en
**px contra los DPI del dispositivo de pintado**, no contra los 96 dpi
lógicos. Como los dos caminos de salida de la app (Descargar PDF e Imprimir)
usan `QPrinter(HighResolution)`, que son 1200 dpi, un `font-size: 20px` salía
impreso de **0,42 mm** de alto en vez de 5,29 mm: el nombre de la entidad, el
título del documento y los pies de página quedaban ilegibles mientras el resto
del texto (que estaba en `pt`) salía bien. De ahí el síntoma reportado, "al
imprimir de forma directa los archivos aparecen con errores de tamaño en la
fuente" -- afectaba a algunos elementos y no a todos.

Los `margin`/`padding` en px NO tienen el problema (se resuelven como px CSS),
así que la corrección fue exclusivamente sobre `font-size`, y estos dos tests
son lo que impide que vuelva:

  * uno mide el documento realmente dispuesto contra una impresora de 1200 dpi
    y contra una de 96, y exige que las alturas de fuente coincidan;
  * el otro lee el módulo y prohíbe la unidad, que es la forma en que el
    defecto se reintroduce (copiando un estilo de una vista, donde px es lo
    correcto).
"""

import os
import pathlib
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextDocument  # noqa: E402
from PySide6.QtPrintSupport import QPrinter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from cas_client import documents  # noqa: E402
from tests.client.test_documents_identity import (  # noqa: E402
    _FakeClient,
    _FakeClientCompleto,
    _FakeLoanCompleto,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _alturas_de_fuente_mm(html: str, modo: str) -> set[float]:
    """Alto real de cada fuente del documento, en milímetros sobre el papel."""
    printer = QPrinter(getattr(QPrinter.PrinterMode, modo))
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(os.devnull)
    documento = QTextDocument()
    documento.setHtml(html)
    documento.documentLayout().setPaintDevice(printer)

    dpi = printer.logicalDpiY()
    alturas = set()
    bloque = documento.begin()
    while bloque.isValid():
        iterador = bloque.begin()
        while not iterador.atEnd():
            fragmento = iterador.fragment()
            if fragmento.isValid() and fragmento.text().strip():
                fuente = fragmento.charFormat().font()
                if fuente.pixelSize() > 0:
                    alto_mm = fuente.pixelSize() / dpi * 25.4
                else:
                    alto_mm = fuente.pointSizeF() / 72 * 25.4
                alturas.add(round(alto_mm, 2))
            iterador += 1
        bloque = bloque.next()
    return alturas


_DOCUMENTOS = {
    "pagare": lambda: documents.pagare_html(_FakeLoanCompleto, _FakeClient),
    "contrato": lambda: documents.contrato_html(_FakeLoanCompleto, _FakeClient),
    "ficha_cliente": lambda: documents.ficha_cliente_html(
        _FakeLoanCompleto, _FakeClientCompleto
    ),
}


@pytest.mark.parametrize("nombre", sorted(_DOCUMENTOS))
def test_el_tamano_de_fuente_no_depende_de_los_dpi_de_la_impresora(app, nombre):
    html = _DOCUMENTOS[nombre]()

    en_impresora = _alturas_de_fuente_mm(html, "HighResolution")
    en_pantalla = _alturas_de_fuente_mm(html, "ScreenResolution")

    assert en_impresora == en_pantalla, (
        f"{nombre}: el documento cambia de tamaño según los DPI "
        f"(1200 dpi: {sorted(en_impresora)} mm, 96 dpi: {sorted(en_pantalla)} mm). "
        "Suele ser un font-size en px."
    )


@pytest.mark.parametrize("nombre", sorted(_DOCUMENTOS))
def test_ninguna_fuente_sale_ilegible_en_el_papel(app, nombre):
    """Cota absoluta, por si alguna vez las dos mediciones coinciden en un
    tamaño igualmente malo: menos de 1,5 mm no se lee."""
    alturas = _alturas_de_fuente_mm(_DOCUMENTOS[nombre](), "HighResolution")

    assert alturas, f"{nombre} no tiene texto"
    assert (
        min(alturas) >= 1.5
    ), f"{nombre}: hay texto de {min(alturas)} mm de alto en el papel impreso"


def test_los_documentos_no_declaran_tamanos_de_fuente_en_px():
    """px es correcto en las vistas (se pintan a 96 dpi) y erróneo en los
    documentos (se imprimen a 1200): la unidad es la forma en que el defecto
    se reintroduce al copiar un estilo de una vista."""
    codigo = pathlib.Path("cas_client/documents.py").read_text(encoding="utf-8")

    en_px = re.findall(r"font-size:\s*\d+px", codigo)

    assert not en_px, (
        "Usá pt en los documentos (1px = 0,75pt); en px el texto se imprime "
        f"a los DPI de la impresora: {en_px}"
    )
