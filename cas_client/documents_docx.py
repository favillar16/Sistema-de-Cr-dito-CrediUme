"""DOCX counterparts of documents.py's loan documents (Liquidación de
Préstamo, Pagaré, Contrato, Cronograma, Comprobante de Pago) -- same data
sources (GetLoanById/GetClientById/GetAmortizationSchedule) and the same legal
clause text, but saved as an editable .docx instead of a flattened PDF so a
user can tweak specific fields (e.g. correct a client address) without
regenerating from the app.

Mirrors documents.py's structure section-by-section; reuses its shared
constants (_COMPANY_*, _TERM_*, _ESTADOS_LABEL) rather than duplicating them,
so the authorised commercial terms can't drift between the PDF and the DOCX
of the same contract."""

import html

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from cas_client import assets, documents, theme
from cas_client.formatting import (
    fecha,
    fecha_hora,
    gs,
    rate_percent,
    rate_percent_mensual,
)


def _rgb(hex_color: str) -> RGBColor:
    hex_color = hex_color.lstrip("#")
    return RGBColor(
        int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    )


_PRIMARY = _rgb(theme.PRIMARY)
_TEXT_PRIMARY = _rgb(theme.TEXT_PRIMARY)
_TEXT_MUTED = _rgb(theme.TEXT_MUTED)


def _add_divider(document: Document) -> None:
    """Thin horizontal rule -- python-docx has no direct API for this, so it's
    built from a paragraph's bottom border (the standard OOXML workaround)."""
    paragraph = document.add_paragraph()
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), theme.BORDER.lstrip("#"))
    pBdr.append(bottom)
    pPr.append(pBdr)


def _add_title(document: Document, title: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(title.upper())
    run.bold = True
    run.font.size = Pt(14)
    run.font.color.rgb = _PRIMARY


def _dias_de_gracia() -> str:
    """ "11 (once) días" -- el plazo desde el que se devenga la mora, en
    dígitos y letras, igual que documents.py."""
    dias = documents._TERM_MORATORY_GRACE_DAYS
    return f"{dias} ({documents._numero_en_letras(dias)}) días"


def _cuotas_para_acelerar() -> str:
    """ "4 (cuatro) cuotas vencidas" -- el umbral que habilita a exigir el
    total adeudado."""
    cuotas = documents._TERM_ACCELERATION_INSTALLMENTS
    return f"{cuotas} ({documents._numero_en_letras(cuotas)}) cuotas vencidas"


def _add_header(document: Document, title: str) -> None:
    """Logo + legal-entity block + centered title, same layout as
    documents.py's _header() / HeaderDocumentos.png.

    Ya no recibe draft_banner: el cartel "BORRADOR -- TEXTO LEGAL PENDIENTE
    DE REVISIÓN" se quitó de los tres documentos que lo llevaban al
    autorizarse el texto legal y cargarse las condiciones reales (ver los
    _TERM_* de documents.py)."""
    table = document.add_table(rows=1, cols=2)
    table.columns[0].width = Inches(1.5)
    table.columns[1].width = Inches(4.8)

    logo_run = table.cell(0, 0).paragraphs[0].add_run()
    logo_run.add_picture(assets.LOGO_FULL_PNG, width=Inches(1.3))

    text_cell = table.cell(0, 1)
    name_run = text_cell.paragraphs[0].add_run(
        f"{documents._COMPANY_NAME} — RUC: {documents._COMPANY_RUC}"
    )
    name_run.bold = True
    name_run.font.size = Pt(12)
    name_run.font.color.rgb = _TEXT_PRIMARY

    addr_paragraph = text_cell.add_paragraph()
    addr_run = addr_paragraph.add_run(documents._COMPANY_ADDRESS)
    addr_run.font.size = Pt(9)
    addr_run.font.color.rgb = _TEXT_MUTED

    phone_paragraph = text_cell.add_paragraph()
    phone_run = phone_paragraph.add_run(f"Cel: {documents._COMPANY_PHONE}")
    phone_run.font.size = Pt(9)
    phone_run.font.color.rgb = _TEXT_MUTED

    _add_divider(document)
    _add_title(document, title)


def _add_labeled_lines(document: Document, lines: list[tuple[str, str]]) -> None:
    for label, value in lines:
        paragraph = document.add_paragraph()
        label_run = paragraph.add_run(f"{label}: ")
        label_run.bold = True
        paragraph.add_run(value)


def _add_client_block(document: Document, client) -> None:
    _add_labeled_lines(
        document,
        [
            ("Cliente", f"{client.first_name} {client.last_name}"),
            ("Documento", client.national_id),
            ("Dirección", client.address),
            ("Teléfono", client.phone_number),
        ],
    )


def _add_section_heading(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = True
    run.font.color.rgb = _PRIMARY


def _add_footer_note(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    run.italic = True
    run.font.size = Pt(9)
    run.font.color.rgb = _TEXT_MUTED


def _add_signature_line(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(36)
    paragraph.add_run(text)


def _add_cargos_y_garantia(document: Document, loan) -> None:
    """BR-LOAN-005/006: composición del crédito + garantía.

    Las filas salen de documents.py's filas_composicion_credito() para que el
    PDF y el DOCX del mismo préstamo no puedan mostrar totales distintos --
    mismo criterio que _filas_reporte() en el reporte de período. Ese módulo
    escribe para HTML, así que las etiquetas vienen con entidades
    (&eacute;...) y hay que desescaparlas antes de meterlas en un docx.
    """
    filas = [
        (html.unescape(nombre), monto, destacar)
        for nombre, monto, destacar in documents.filas_composicion_credito(loan)
    ]

    _add_section_heading(document, "Composición del crédito")
    table = document.add_table(rows=1 + len(filas), cols=2)
    table.style = "Table Grid"
    table.rows[0].cells[0].text = "Concepto"
    table.rows[0].cells[1].text = "Monto (Gs)"
    for row_index, (nombre, monto, destacar) in enumerate(filas, start=1):
        cells = table.rows[row_index].cells
        cells[0].text = nombre
        cells[1].text = monto
        if destacar:
            for cell in cells:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.bold = True
    _add_footer_note(
        document,
        "Los cargos y seguros se financian junto con el capital: integran el "
        "total del crédito, que es el monto que amortizan las cuotas y sobre "
        "el que se calcula el interés. El importe entregado al cliente es el "
        "capital solicitado.",
    )

    if loan.guarantee_type:
        _add_labeled_lines(
            document,
            [
                (
                    "Garantía",
                    f"{loan.guarantee_type} — Monto aplicado: "
                    f"{gs(loan.guarantee_amount)}",
                ),
            ],
        )


def comprobante_pago_docx(loan, client, payment) -> Document:
    """DOCX de documents.py's comprobante_pago_html() -- BR-LOAN-011. Sin
    banner de borrador, mismo criterio que el Cronograma: no tiene texto legal
    a revisar, solo el detalle de un pago ya registrado."""
    document = Document()
    _add_header(document, "Comprobante de Pago")
    _add_client_block(document, client)
    _add_labeled_lines(document, [("Préstamo", loan.id)])

    filas = [
        ("Monto abonado", gs(payment.amount_paid)),
        (
            "Cuota(s) abonada(s)",
            documents.cuotas_cubiertas_texto(
                payment.covered_installments, payment.total_installments
            ),
        ),
        # Misma hora local que la versión HTML del comprobante -- ver la nota
        # en documents.comprobante_pago_html sobre por qué no es un strftime
        # directo sobre el naive UTC que devuelve ToDatetime().
        ("Fecha y hora del pago", fecha_hora(payment.paid_at.ToDatetime())),
        *documents.filas_medio_de_pago(payment),  # BR-CAJA-004
        ("Total pagado del préstamo", gs(payment.total_paid)),
        ("Saldo restante", gs(payment.remaining_balance)),
    ]
    table = document.add_table(rows=1 + len(filas), cols=2)
    table.style = "Table Grid"
    table.rows[0].cells[0].text = "Concepto"
    table.rows[0].cells[1].text = "Detalle"
    for row_index, (concepto, detalle) in enumerate(filas, start=1):
        cells = table.rows[row_index].cells
        cells[0].text = concepto
        cells[1].text = detalle

    if payment.status == "PAID":
        paragraph = document.add_paragraph()
        run = paragraph.add_run("Con este pago el préstamo queda totalmente cancelado.")
        run.bold = True
        run.font.color.rgb = _rgb(theme.SUCCESS)

    _add_labeled_lines(
        document,
        [
            (
                "Registrado por",
                documents.responsable(
                    payment.recorded_by_name, payment.recorded_by_national_id
                ),
            )
        ],
    )
    _add_footer_note(
        document,
        "Comprobante emitido por el sistema de CREDIMED UME. El saldo restante "
        "puede variar por cargos o ajustes posteriores a la fecha de emisión de "
        "este comprobante.",
    )
    return document


def ficha_cliente_docx(loan, client) -> Document:
    """DOCX de documents.py's ficha_cliente_html() -- ver ese docstring para
    el razonamiento (ficha de análisis para la decisión de aprobación,
    reúne datos del cliente + referencias + préstamo solicitado en un solo
    papel). Sin banner de borrador ni cláusulas legales, mismo criterio que
    cronograma_docx."""
    document = Document()
    _add_header(document, "Ficha de Cliente — Análisis de Crédito")

    _add_section_heading(document, "Datos personales")
    _add_labeled_lines(
        document,
        [
            ("Nombre completo", f"{client.first_name} {client.last_name}"),
            ("Documento (C.I.)", client.national_id),
            ("Fecha de nacimiento", fecha(client.date_of_birth)),
            ("Estado del cliente", "Activo" if client.is_active else "Inactivo"),
            ("Cliente desde", fecha_hora(client.created_at.ToDatetime())),
            ("Email", client.email),
            ("Teléfono", client.phone_number),
            ("Dirección", client.address),
        ],
    )

    _add_section_heading(document, "Situación financiera declarada")
    _add_labeled_lines(
        document,
        [
            (
                "Ingreso mensual declarado",
                gs(client.declared_monthly_income) or "No registrado",
            ),
            ("Origen de fondos", client.source_of_funds or "No registrado"),
        ],
    )

    _add_section_heading(document, "Referencias")
    table = document.add_table(rows=4, cols=4)
    table.style = "Table Grid"
    for col, text in enumerate(["Tipo", "Nombre", "Relación / Cargo", "Teléfono"]):
        table.rows[0].cells[col].text = text
    filas = [
        (
            "Referencia personal 1",
            client.personal_reference_1_name,
            client.personal_reference_1_relationship,
            client.personal_reference_1_phone,
        ),
        (
            "Referencia personal 2",
            client.personal_reference_2_name,
            client.personal_reference_2_relationship,
            client.personal_reference_2_phone,
        ),
        (
            "Referencia laboral",
            client.employment_reference_employer,
            f"{client.employment_reference_position} "
            f"({client.employment_reference_seniority})",
            client.employment_reference_phone,
        ),
    ]
    for row_index, fila in enumerate(filas, start=1):
        cells = table.rows[row_index].cells
        for col, valor in enumerate(fila):
            cells[col].text = valor

    _add_section_heading(document, "Préstamo solicitado")
    garantia = (
        f"{loan.guarantee_type} — Monto: {gs(loan.guarantee_amount)}"
        if loan.guarantee_type
        else "Sin garantía registrada"
    )
    _add_labeled_lines(
        document,
        [
            ("Número", loan.id),
            ("Estado", documents._ESTADOS_LABEL.get(loan.status, loan.status)),
            ("Capital solicitado", gs(loan.principal_amount)),
            ("Plazo", f"{loan.term_months} meses"),
            (
                "Tasa de interés",
                f"{rate_percent(loan.interest_rate)} anual "
                f"({rate_percent_mensual(loan.interest_rate)} mensual)",
            ),
            ("Cuota mensual", gs(loan.installment_amount)),
            (
                "Total del crédito (capital + cargos)",
                gs(loan.total_credit_with_charges),
            ),
            ("Total a pagar", gs(loan.total_to_pay)),
            ("Garantía", garantia),
        ],
    )

    ratio_texto, ratio_excede = documents._relacion_cuota_ingreso(loan, client)
    ratio_paragraph = document.add_paragraph()
    ratio_run = ratio_paragraph.add_run(f"Relación cuota/ingreso: {ratio_texto}")
    ratio_run.bold = True
    if ratio_excede:
        ratio_run.font.color.rgb = _rgb(theme.ERROR)

    _add_footer_note(
        document,
        "Ficha generada por el sistema de CREDIMED UME. Uso interno para el "
        "análisis y la decisión de aprobación del crédito -- no constituye un "
        "documento legal ni se entrega al cliente.",
    )
    return document


def reporte_periodo_docx(report, generated_by: str = "") -> Document:
    """BR-DASH-002, contraparte .docx de documents.reporte_periodo_html().
    Sin banner de borrador (no tiene texto legal, solo cifras calculadas),
    igual criterio que cronograma_docx.

    report: dashboard_service_pb2.GetPeriodReportResponse"""
    document = Document()
    _add_header(document, "Reporte de Cierre de Período")

    lineas = [
        ("Período", f"del {fecha(report.start_date)} al {fecha(report.end_date)}")
    ]
    if generated_by:
        lineas.append(("Generado por", generated_by))
    _add_labeled_lines(document, lineas)

    # Misma definición de contenido que el PDF -- ver documents._filas_reporte.
    filas = documents._filas_reporte(report)
    table = document.add_table(rows=1 + len(filas), cols=3)
    table.style = "Table Grid"
    for col, text in enumerate(["Sección", "Concepto", "Valor"]):
        table.rows[0].cells[col].text = text
    for row_index, (seccion, concepto, valor) in enumerate(filas, start=1):
        cells = table.rows[row_index].cells
        cells[0].text = seccion
        cells[1].text = concepto
        cells[2].text = valor

    _add_footer_note(
        document,
        '"Movimiento" y "Cobranza" miden lo ocurrido dentro del período '
        'seleccionado. "Situación al cierre" es una foto del estado actual de '
        "la cartera al momento de generar este reporte, no del último día del "
        "período.",
    )
    _add_footer_note(
        document, "Documento generado por el sistema de CREDIMED UME. Uso interno."
    )
    return document


def _apaisar(document: Document) -> None:
    """Pasa la hoja a orientación horizontal.

    Cambiar `orientation` sola no alcanza: Word toma el tamaño de página de
    page_width/page_height, así que hay que intercambiarlos además, o sale una
    hoja vertical declarada como horizontal.
    """
    seccion = document.sections[0]
    ancho, alto = seccion.page_width, seccion.page_height
    seccion.orientation = WD_ORIENT.LANDSCAPE
    seccion.page_width, seccion.page_height = alto, ancho


def reporte_estado_pagos_docx(report, generated_by: str = "") -> Document:
    """BR-DASH-003, contraparte .docx de documents.reporte_estado_pagos_html().
    Sin banner de borrador, igual criterio que reporte_periodo_docx.

    Es el único documento apaisado: su tabla tiene 8 columnas (el resto tiene
    2 o 3), y en vertical las celdas se parten en varias líneas cada una hasta
    volver ilegible la fila. El PDF hace lo propio poniendo la impresora en
    horizontal -- ver dashboard_view.py.

    report: dashboard_service_pb2.GetClientPaymentStatusReportResponse"""
    document = Document()
    _apaisar(document)
    _add_header(document, "Estado de Pago de Clientes")

    alcance = (
        "Solo clientes con cuotas vencidas o préstamos incumplidos"
        if report.only_overdue
        else "Todos los clientes con cartera viva (préstamos activos o incumplidos)"
    )
    lineas = [
        ("Fecha del reporte", fecha_hora(report.generated_at.ToDatetime())),
        ("Alcance", alcance),
        ("Clientes informados", str(report.clients_count)),
        ("Con atraso", str(report.overdue_clients_count)),
        ("Saldo pendiente", gs(report.total_outstanding)),
        ("Monto vencido", gs(report.total_overdue)),
    ]
    if generated_by:
        lineas.append(("Generado por", generated_by))
    _add_labeled_lines(document, lineas)

    # Mismas columnas y mismas filas que el PDF y que la tabla de la vista --
    # ver documents.ESTADO_PAGOS_COLUMNAS / _filas_estado_pagos.
    columnas = documents.ESTADO_PAGOS_COLUMNAS
    filas = documents._filas_estado_pagos(report)
    table = document.add_table(rows=1 + len(filas), cols=len(columnas))
    table.style = "Table Grid"
    for col, text in enumerate(columnas):
        table.rows[0].cells[col].text = text
    for row_index, tupla in enumerate(filas, start=1):
        cells = table.rows[row_index].cells
        for col, celda in enumerate(tupla):
            cells[col].text = celda

    _add_footer_note(
        document,
        'El "monto vencido" es lo ya exigible e impago; el "saldo pendiente" '
        "incluye además las cuotas futuras todavía no vencidas. Los totales "
        "corresponden a los clientes listados, no a toda la cartera.",
    )
    _add_footer_note(
        document, "Documento generado por el sistema de CREDIMED UME. Uso interno."
    )
    return document


def liquidacion_docx(loan, client, schedule) -> Document:
    """loan: loan_service_pb2.GetLoanByIdResponse
    client: client_service_pb2.GetClientByIdResponse
    schedule: loan_service_pb2.GetAmortizationScheduleResponse"""
    document = Document()
    _add_header(document, "Liquidación de Préstamo")
    _add_client_block(document, client)

    estado = documents._ESTADOS_LABEL.get(loan.status, loan.status)
    _add_labeled_lines(
        document,
        [
            ("Préstamo", loan.id),
            ("Estado", estado),
            ("Capital", gs(loan.principal_amount)),
            (
                "Tasa de interés",
                f"{rate_percent(loan.interest_rate)} anual "
                f"({rate_percent_mensual(loan.interest_rate)} mensual sobre "
                "el monto original)",
            ),
            ("Plazo", f"{loan.term_months} meses"),
            ("Total pagado", gs(loan.total_paid)),
            ("Saldo restante", gs(loan.remaining_balance)),
        ],
    )

    _add_cargos_y_garantia(document, loan)

    _add_section_heading(document, "Cronograma de amortización")
    table = document.add_table(rows=1 + len(schedule.installments), cols=5)
    table.style = "Table Grid"
    for col, text in enumerate(
        ["Cuota", "Monto (Gs)", "Capital (Gs)", "Interés (Gs)", "Saldo (Gs)"]
    ):
        table.rows[0].cells[col].text = text
    for row_index, installment in enumerate(schedule.installments, start=1):
        cells = table.rows[row_index].cells
        cells[0].text = str(installment.installment_number)
        cells[1].text = gs(installment.payment_amount)
        cells[2].text = gs(installment.principal_portion)
        cells[3].text = gs(installment.interest_portion)
        cells[4].text = gs(installment.remaining_balance)

    _add_footer_note(
        document,
        "Documento generado por el sistema de CREDIMED UME. Válido únicamente junto "
        "con la firma y sello de la entidad.",
    )
    return document


def _add_garantia_line(document: Document, loan) -> None:
    """Línea de codeudor/garantía, mismo criterio que documents.py's
    _garantia_linea() (BR-LOAN-005 no distingue codeudor de garantía en
    general)."""
    if loan.guarantee_type:
        valor = f"{loan.guarantee_type} — Monto: {gs(loan.guarantee_amount)}"
    else:
        valor = "Sin garantía registrada"
    _add_labeled_lines(document, [("Garantía / Codeudor solidario", valor)])


def pagare_docx(loan, client) -> Document:
    document = Document()
    _add_header(document, "Pagaré a la Orden")
    _add_client_block(document, client)
    _add_garantia_line(document, loan)

    body = document.add_paragraph()
    body.add_run(f"DECLARO(AMOS) ADEUDAR a {documents._COMPANY_NAME} la suma de ")
    amount_run = body.add_run(f"Guaraníes {gs(loan.total_credit_with_charges)}")
    amount_run.bold = True
    body.add_run(
        f", integrada por un capital de {gs(loan.principal_amount)} y "
        f"{gs(loan.total_charges)} en concepto de cargos y seguros "
        "financiados, que PAGARÉ(MOS) solidariamente, a su orden, libre de "
        "gastos y sin "
        f"protesto, en {loan.term_months} cuotas iguales, mensuales y "
        "consecutivas, con vencimiento la primera de ellas el día "
        f"{fecha(loan.first_due_date)}, y las siguientes cuotas en esas mismas "
        f"fechas de los meses subsiguientes hasta su total cancelación, en "
        f"el domicilio de {documents._COMPANY_NAME}, sito en "
        f"{documents._COMPANY_ADDRESS}."
    )

    interest_paragraph = document.add_paragraph()
    interest_paragraph.add_run(
        "Queda expresamente pactado que los importes de las cuotas "
        "documentadas en este instrumento devengarán un interés "
        f"compensatorio del {rate_percent_mensual(loan.interest_rate)} "
        "mensual, calculado sobre el monto original del préstamo."
    )

    mora_paragraph = document.add_paragraph()
    mora_paragraph.add_run(
        "En caso de mora se aplicará, sobre cada cuota vencida e impaga, un "
        "interés moratorio en carácter punitorio del "
        f"{documents._TERM_MORATORY_RATE}, que se devengará a partir de los "
        f"{_dias_de_gracia()} corridos contados desde la fecha de su primer "
        "vencimiento."
    )

    acceleration_paragraph = document.add_paragraph()
    acceleration_paragraph.add_run(
        f"La falta de pago de {_cuotas_para_acelerar()} facultará a "
        f"{documents._COMPANY_NAME} a exigir el total adeudado, inclusive "
        "las cuotas no vencidas, produciéndose la mora por el mero "
        "vencimiento del plazo, sin necesidad de ningún requerimiento "
        "judicial y/o extrajudicial."
    )

    jurisdiction_paragraph = document.add_paragraph()
    jurisdiction_paragraph.add_run(
        "Todas las partes intervinientes en este documento se someten a la "
        "jurisdicción y competencia de los Jueces y Tribunales de "
        f"{documents._TERM_JURISDICTION_CITY}."
    )

    _add_labeled_lines(document, [("Préstamo", loan.id)])
    _add_signature_line(document, "Firma del deudor: ______________________________")
    _add_footer_note(document, "Lugar y fecha: ______________________________")
    return document


def cronograma_docx(loan, client, schedule) -> Document:
    """DOCX de documents.py's cronograma_html() -- ver ese docstring para el
    razonamiento (documento standalone para entregar al cliente, con nombre
    del cliente y asesor responsable)."""
    document = Document()
    _add_header(document, "Cronograma de Pago")
    _add_client_block(document, client)

    asesor = documents.responsable(
        loan.created_by_full_name,
        loan.created_by_national_id,
        respaldo=loan.created_by_username,
    )
    _add_labeled_lines(
        document,
        [
            ("Asesor responsable", asesor),
            ("Préstamo", loan.id),
            ("Capital", gs(loan.principal_amount)),
            ("Tasa de interés", rate_percent(loan.interest_rate)),
            ("Plazo", f"{loan.term_months} meses"),
            ("Primer vencimiento", fecha(loan.first_due_date)),
        ],
    )

    table = document.add_table(rows=1 + len(schedule.installments), cols=6)
    table.style = "Table Grid"
    for col, text in enumerate(
        [
            "Cuota",
            "Vencimiento",
            "Monto (Gs)",
            "Capital (Gs)",
            "Interés (Gs)",
            "Saldo (Gs)",
        ]
    ):
        table.rows[0].cells[col].text = text
    for row_index, installment in enumerate(schedule.installments, start=1):
        cells = table.rows[row_index].cells
        cells[0].text = str(installment.installment_number)
        cells[1].text = fecha(installment.due_date)
        cells[2].text = gs(installment.payment_amount)
        cells[3].text = gs(installment.principal_portion)
        cells[4].text = gs(installment.interest_portion)
        cells[5].text = gs(installment.remaining_balance)

    _add_footer_note(
        document,
        "Este cronograma es informativo y está sujeto a los términos y "
        "condiciones establecidos en el Pagaré y el Contrato de Préstamo "
        "firmados. Copia entregada al cliente.",
    )
    return document


def contrato_docx(loan, client) -> Document:
    document = Document()
    _add_header(document, "Contrato de Préstamo")

    intro = document.add_paragraph()
    intro.add_run(
        f"Entre {documents._COMPANY_NAME}, con RUC {documents._COMPANY_RUC}, "
        f"con domicilio en {documents._COMPANY_ADDRESS}, en adelante "
        f"{documents._COMPANY_NAME} o LA ENTIDAD, por una parte; y por la "
        "otra el(la/los) Sr(a)(es). abajo identificado(s), en adelante "
        "EL(LOS) PRESTATARIO(S), convienen en celebrar el presente Contrato "
        "de Préstamo de Dinero, sujeto a las cláusulas y condiciones "
        "siguientes."
    )

    _add_client_block(document, client)
    _add_garantia_line(document, loan)

    _add_section_heading(document, "Cláusulas")

    clauses = [
        (
            "Primera (Objeto)",
            f"{documents._COMPANY_NAME} otorga al(los) Prestatario(s) un "
            f"préstamo de dinero por un capital de Guaraníes "
            f"{gs(loan.principal_amount)}, que se desembolsa a la firma del "
            f"presente instrumento. Al capital se adicionan "
            f"{gs(loan.total_charges)} en concepto de cargos, gastos "
            "administrativos y seguros, que se financian junto con él, de "
            "modo que el monto total del crédito asciende a Guaraníes "
            f"{gs(loan.total_credit_with_charges)}, importe por el cual se "
            "suscribe un Pagaré a la orden destinado a servir como título "
            "de crédito.",
        ),
        (
            "Segunda (Reembolso)",
            "El(Los) Prestatario(s) se compromete(n) a reembolsar el "
            f"préstamo otorgado en {loan.term_months} cuotas iguales, "
            "mensuales y consecutivas: cada cuota amortiza una porción "
            "constante del monto total del crédito e incluye un interés fijo "
            "calculado sobre ese mismo monto original, de modo que todas las "
            "cuotas son del mismo importe. La primera de ellas vence el día "
            f"{fecha(loan.first_due_date)}, mediante transferencia bancaria, "
            "débito directo o descuento en cuenta, según lo acordado.",
        ),
        (
            "Tercera (Intereses)",
            "Se acuerda el pago de un interés compensatorio del "
            f"{rate_percent_mensual(loan.interest_rate)} mensual, "
            "calculado sobre el monto original del préstamo y abonado junto "
            "con las cuotas de amortización del capital. Para el caso de falta de pago en la fecha "
            "convenida, se aplicará además, sobre cada cuota vencida e "
            "impaga, un interés moratorio en carácter punitorio del "
            f"{documents._TERM_MORATORY_RATE}, que se devengará a partir de "
            f"los {_dias_de_gracia()} corridos contados desde la fecha de su "
            "primer vencimiento.",
        ),
        (
            "Cuarta (Mora y vencimiento anticipado)",
            "La mora se producirá por el mero vencimiento de los plazos, "
            "sin necesidad de interpelación judicial alguna. La falta de "
            f"pago de {_cuotas_para_acelerar()} hará decaer de pleno "
            "derecho todos los plazos estipulados para el pago, facultando "
            f"a {documents._COMPANY_NAME} a declarar vencidas todas las "
            "cuotas y exigir el pago de la totalidad de la deuda, "
            "ejecutando para el efecto el Pagaré suscripto junto con este "
            "contrato.",
        ),
        (
            "Quinta (Central de riesgo crediticio)",
            "El(Los) Prestatario(s) autoriza(n) expresamente a "
            f"{documents._COMPANY_NAME} para que, por su cuenta o a través "
            "de terceros, recabe información sobre su situación "
            "patrimonial y crediticia, y para que, en caso de atraso en el "
            "pago, informe sus datos a las centrales de riesgo crediticio "
            "correspondientes, conforme a la legislación vigente.",
        ),
        (
            "Sexta (Jurisdicción)",
            "Todas las partes intervinientes en este contrato se someten a "
            "la jurisdicción y competencia de los Jueces y Tribunales de "
            f"{documents._TERM_JURISDICTION_CITY}.",
        ),
    ]
    for heading, text in clauses:
        paragraph = document.add_paragraph()
        heading_run = paragraph.add_run(f"{heading}: ")
        heading_run.bold = True
        paragraph.add_run(text)

    _add_labeled_lines(document, [("Préstamo", loan.id)])
    _add_signature_line(document, "Firma del deudor: ______________________________")
    _add_footer_note(
        document, f"Firma de {documents._COMPANY_NAME}: ______________________________"
    )
    return document
