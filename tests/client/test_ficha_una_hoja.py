"""La Ficha de Cliente tiene que entrar en UNA hoja.

Es un formulario que se imprime, se completa a mano y se archiva en el
legajo: partido en dos hojas, la mitad de abajo (dictamen, montos aprobados,
firmas) queda separada de los datos que la justifican.

**Por qué este archivo existe.** Hasta 2026-09-22 la única afirmación de que
la ficha entraba en una hoja era una línea de CLAUDE.md ("~261 mm de ~267
usables"), y estaba muy equivocada: medida de verdad, la ficha necesitaba
**340 mm** de contenido con datos realistas, contra los 281 mm útiles de un
A4. Salía en dos hojas siempre, y lo reportó la entidad.

**Cómo se mide, y por qué así.** Se imprime a un PDF de verdad y se cuentan
sus páginas. El atajo tentador -- `QTextDocument.setPageSize(...)` y leer
`document.size()` sin adjuntar el dispositivo de impresión -- da números que
no tienen nada que ver: sobre esta misma ficha informaba 250 mm cuando el PDF
real salía en dos hojas. Un test que mide distinto de lo que imprime no es un
test.

**Por qué se mide contra A4 aunque la entidad también use Oficio.** A4
(210 × 297) es más chica que Oficio (216 × 330) en las dos dimensiones, así
que lo que entra en A4 entra en Oficio. Medir la más chica cubre las dos.

**Por qué hay un caso de datos largos.** El fixture compartido
(`_FakeClientCompleto`) tiene valores irrealmente cortos -- una dirección de
17 caracteres, "Salario" como origen de fondos. Con esos datos la ficha entra
con holgura incluso cuando no entra en la realidad; la segunda prueba usa
valores del largo que tienen los clientes de verdad, que es el caso que falla
primero.
"""

import re

import pytest
from PySide6.QtCore import QMarginsF, QSizeF
from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

from cas_client import documents
from cas_client.page_layout import _MARGEN_MM
from tests.client.test_documents_identity import (
    _FakeClientCompleto,
    _FakeLoanCompleto,
)

_A4_ALTO_MM = 297.0
_A4_ANCHO_MM = 210.0

# Margen de seguridad: la ficha también tiene que entrar en una hoja
# `_HOLGURA_MM` más corta que un A4. Las fuentes de la marca (Comfortaa,
# Inter) no están instaladas ni acá ni en las PCs de la entidad, así que la
# medición usa el mismo fallback que la impresión real -- pero una fuente
# distinta, o un dato un poco más largo que el del caso de prueba, mueven el
# alto unos milímetros. Exigir que entre justo sería exigir que entre por
# casualidad.
_HOLGURA_MM = 8.0


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _ClienteConDatosLargos:
    """El fixture compartido, con los campos de texto libre al largo que
    tienen en la realidad: direcciones paraguayas completas (avenida, calle
    transversal, número y barrio), nombres compuestos con dos apellidos, y un
    origen de fondos descrito en una frase en vez de una palabra."""

    def __getattr__(self, nombre):
        return getattr(_FakeClientCompleto, nombre)

    first_name = "Maria Esperanza"
    last_name = "Villalba de Benitez"
    address = "Avda. Mcal. Estigarribia c/ Cnel. Yegros N 2451, B San Blas"
    email = "maria.villalba@gmail.com"
    source_of_funds = "Comercio de indumentaria en el Mercado Municipal"
    employment_reference_employer = "Cooperativa Coronel Oviedo Ltda."
    employment_reference_position = "Encargada de cobranzas"


def _paginas(html: str, ruta, alto_mm: float = _A4_ALTO_MM) -> int:
    """Páginas que ocupa `html` impreso en una hoja de `alto_mm`.

    Imprime a PDF con el mismo `QPrinter(HighResolution)` y los mismos
    márgenes que usan las vistas, y lee el `/Count` del árbol de páginas del
    archivo resultante. Es la única medición que coincide con lo que sale por
    la impresora.
    """
    document = QTextDocument()
    document.setHtml(html)

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(ruta))
    printer.setPageLayout(
        QPageLayout(
            QPageSize(QSizeF(_A4_ANCHO_MM, alto_mm), QPageSize.Unit.Millimeter),
            QPageLayout.Orientation.Portrait,
            QMarginsF(_MARGEN_MM, _MARGEN_MM, _MARGEN_MM, _MARGEN_MM),
            QPageLayout.Unit.Millimeter,
        )
    )
    document.print_(printer)

    with open(ruta, "rb") as archivo:
        return int(re.search(rb"/Count\s+(\d+)", archivo.read()).group(1))


@pytest.mark.parametrize(
    "cliente, caso",
    [
        (_FakeClientCompleto, "datos del fixture"),
        (_ClienteConDatosLargos(), "datos largos realistas"),
    ],
)
def test_la_ficha_entra_en_una_hoja(app, tmp_path, cliente, caso):
    html = documents.ficha_cliente_html(_FakeLoanCompleto, cliente)

    paginas = _paginas(html, tmp_path / "ficha.pdf")

    assert paginas == 1, (
        f"La ficha con {caso} sale en {paginas} hojas. Hay que recortar alto "
        "(los cuerpos de `_banda`/`_celda`, el encabezado y el pie son las "
        "palancas que más rinden, medidas), no subir el umbral."
    )


@pytest.mark.parametrize(
    "cliente, caso",
    [
        (_FakeClientCompleto, "datos del fixture"),
        (_ClienteConDatosLargos(), "datos largos realistas"),
    ],
)
def test_la_ficha_entra_con_holgura(app, tmp_path, cliente, caso):
    """Entra además en una hoja `_HOLGURA_MM` más corta que el A4.

    Sin esto, "entra" puede significar "entra por medio milímetro", y el
    primer cliente con un apellido más largo la vuelve a partir en dos.
    """
    html = documents.ficha_cliente_html(_FakeLoanCompleto, cliente)

    paginas = _paginas(
        html, tmp_path / "ficha_holgura.pdf", alto_mm=_A4_ALTO_MM - _HOLGURA_MM
    )

    assert paginas == 1, (
        f"La ficha con {caso} entra en un A4 pero no en uno {_HOLGURA_MM} mm "
        "más corto: está al límite y cualquier dato un poco más largo la parte."
    )


def test_la_hoja_declarada_es_a4(app):
    """Sin tamaño declarado, el PDF hereda el papel de la impresora
    predeterminada de Windows y el .docx sale en Carta -- que fue la otra
    mitad de la causa del desborde, no sólo el diseño de la ficha. Si alguien
    vuelve a sacar aplicar_hoja() de las vistas, la ficha entra acá y falla
    allá."""
    from cas_client.page_layout import aplicar_hoja

    printer = QPrinter(QPrinter.PrinterMode.ScreenResolution)
    aplicar_hoja(printer)

    tamano = printer.pageLayout().pageSize().sizePoints()
    # A4 en puntos PostScript, redondeado como lo reporta Qt.
    assert (tamano.width(), tamano.height()) == (595, 842)
