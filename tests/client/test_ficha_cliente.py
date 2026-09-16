"""La ficha de cliente es un formulario, no un informe.

Se imprime para estudiar al solicitante antes de aprobarle el crédito, así que
tiene dos mitades con destinos distintos: arriba, los datos que el sistema ya
tiene (y que por lo tanto no se completan a mano); abajo, el espacio de uso
exclusivo de la entidad, que sale **en blanco** a propósito porque el
dictamen, las condiciones aprobadas y las firmas se ponen sobre el papel.

El formato sigue el de `docs/cambios finales/Ejemplo de Ficha de Clientes.png`
-- secciones numeradas con banda, grilla de campos y bloque interno al pie.
De ese modelo se tomó la estructura; el contenido y la marca son de esta
entidad.
"""

from cas_client import documents, documents_docx
from tests.client.test_documents_identity import (
    _FakeClientCompleto,
    _FakeLoanCompleto,
    _texto_plano,
)


def _ficha() -> str:
    return _texto_plano(
        documents.ficha_cliente_html(_FakeLoanCompleto, _FakeClientCompleto)
    )


def test_las_secciones_van_numeradas_como_en_el_formulario():
    texto = _ficha()

    for numero, titulo in (
        (1, "DATOS GENERALES DEL CLIENTE"),
        (2, "SITUACIÓN FINANCIERA DECLARADA"),
        (3, "REFERENCIAS"),
        (4, "CRÉDITO SOLICITADO"),
        (5, "ESPACIO PARA USO EXCLUSIVO DE CREDIMED UME"),
    ):
        assert f"{numero}. {titulo}" in texto


def test_trae_los_datos_que_se_estudian_para_decidir():
    texto = _ficha()

    # Identidad y contacto.
    assert "Fabrizio Villar" in texto
    assert "5746680" in texto
    # Capacidad de pago: el ingreso declarado, la cuota y la relación entre
    # ambas, que es el número por el que pasa BR-LOAN-002.
    assert "5.000.000 Gs" in texto
    assert "1.768.056 Gs" in texto
    assert "35.4% del ingreso declarado" in texto
    # Origen de fondos (BR-CLI-006) y las tres referencias (BR-CLI-005).
    assert "Salario" in texto
    assert "Juan Pérez" in texto
    assert "María López" in texto
    assert "ACME S.A." in texto


def test_declara_el_credito_con_los_cargos_financiados():
    """BR-LOAN-006: quien aprueba tiene que ver el total que el cliente
    devuelve, no sólo el capital que recibe en mano."""
    texto = _ficha()

    assert "18.000.000 Gs" in texto  # capital solicitado
    assert "1.000.000 Gs" in texto  # cargos financiados
    assert "19.000.000 Gs" in texto  # total del crédito
    assert "31.825.000 Gs" in texto  # total a pagar


def test_el_bloque_interno_queda_en_blanco_para_completar_a_mano():
    texto = _ficha()

    assert "Dictamen" in texto
    for opcion in ("Aprobado", "Aprobado con modificaciones", "Rechazado"):
        assert opcion in texto
    assert "Monto aprobado" in texto
    assert "Analista que estudió el legajo" in texto
    assert "Responsable que autoriza" in texto
    assert "Firma y aclaración" in texto
    assert "Observaciones" in texto


def test_la_relacion_cuota_ingreso_se_marca_cuando_supera_el_tope():
    """El dato que decide la aprobación no puede salir del mismo color que el
    resto: si supera el 40% de BR-LOAN-002 tiene que saltar a la vista."""

    class _IngresoBajo(_FakeClientCompleto):
        declared_monthly_income = "1000000.00"

    html = documents.ficha_cliente_html(_FakeLoanCompleto, _IngresoBajo)

    assert "supera el 40%" in _texto_plano(html)
    assert "#C0392B" in html  # theme.ERROR


def test_no_se_entrega_al_cliente_y_lo_dice():
    assert "ni se entrega al cliente" in _ficha()


def test_el_docx_lleva_el_mismo_bloque_de_decision():
    """El DOCX existe para editarse a mano; sin el bloque interno describiría
    la solicitud pero no dejaría constancia de la decisión."""
    documento = documents_docx.ficha_cliente_docx(
        _FakeLoanCompleto, _FakeClientCompleto
    )

    interno = documento.tables[-1]
    dictamen = interno.rows[0].cells[0].text
    assert dictamen.count("☐") == 3
    assert "Aprobado" in dictamen and "Rechazado" in dictamen
    assert "Firma y aclaración" in interno.rows[1].cells[0].text
    assert "Observaciones" in interno.rows[2].cells[0].text
