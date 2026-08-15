"""HTML templates for the four loan documents (Liquidación de Préstamo,
Pagaré, Contrato, Cronograma de Pago), rendered client-side from data already
available via existing RPCs (GetLoanById/GetClientById/GetAmortizationSchedule)
-- no server changes needed for the first three; the fourth needed
Loan.created_by_username (see loan_service.proto).

The Pagaré/Contrato clause structure was adapted from a real, signed loan
pagaré/contrato pair provided as a reference (docs/pagare credi ume.docx,
docs/contrato.pdf) so the *shape* of the documents (declaración de deuda,
cuotas iguales y consecutivas, interés compensatorio/moratorio/punitorio,
cláusula de mora con vencimiento anticipado, autorización a centrales de
riesgo, sometimiento a jurisdicción) matches what a real Paraguayan lending
document actually contains -- but the reference belongs to a different
institution (a different company name/RUC) and a specific real borrower, so
neither was copied: the company identity below is CREDIMED UME's own real
registered data, and every borrower-identifying field is pulled from
`client`/`loan`, never hardcoded.

The commercial terms in those clauses (compensatory rate, moratory/punitive
rate and its grace period, the number of overdue installments that lets the
lender accelerate, and the jurisdiction city) are no longer placeholders: they
are the terms CREDIMED UME authorised, and the draft banner these documents
used to carry was removed on that authorisation. See the _TERM_* constants
below -- they are the single place to change if the entity revises its terms,
and tests/client/test_documents_identity.py guards them against a silent
revert, the same way it guards the legal identity."""

from cas_client import assets, theme
from cas_client.formatting import fecha, gs, rate_percent, rate_percent_mensual

# Datos reales de la entidad, según su inscripción ante la DNIT. Estos ya no
# son placeholders: reemplazan al nombre/RUC de relleno que traía el header de
# HeaderDocumentos.png ("80XXXXXXX-X"). Lo que sigue pendiente de revisión
# legal es el *texto de las cláusulas*, no la identidad de la entidad.
_COMPANY_NAME = "CREDIMED UME"
_COMPANY_RUC = "1276703-4"
_COMPANY_ADDRESS = "Ayolas c/ Acaray — Coronel Oviedo, Paraguay"
_COMPANY_PHONE = "(0984) 319243"

# Condiciones comerciales autorizadas por la entidad. Ya no son placeholders:
# se imprimen tal cual en el Pagaré y el Contrato que firma el cliente, así que
# cambiarlas cambia el instrumento legal -- no tocar sin autorización expresa.
#
# La mora se cobra como UN solo interés, moratorio con carácter punitorio, sobre
# cada cuota vencida (no hay un segundo interés separado sobre saldos deudores:
# la entidad definió una sola tasa). Por eso hay un único _TERM_MORATORY_RATE
# donde antes había un par moratoria/punitoria.
_TERM_MORATORY_RATE = "0,38% mensual"
_TERM_MORATORY_GRACE_DAYS = 11
_TERM_ACCELERATION_INSTALLMENTS = 4
_TERM_JURISDICTION_CITY = "Coronel Oviedo"

# El interés compensatorio NO es constante acá: sale de loan.interest_rate, para
# que el documento no pueda contradecir la cuota que el sistema realmente
# calculó. Con la tasa fija vigente (config.LOAN_FIXED_INTEREST_RATE = 0.18)
# rate_percent_mensual() imprime "1,5%", la tasa mensual autorizada.

_NUMEROS_EN_LETRAS = {
    1: "uno",
    2: "dos",
    3: "tres",
    4: "cuatro",
    5: "cinco",
    6: "seis",
    7: "siete",
    8: "ocho",
    9: "nueve",
    10: "diez",
    11: "once",
    12: "doce",
    15: "quince",
    30: "treinta",
    60: "sesenta",
    90: "noventa",
}


def _numero_en_letras(valor: int) -> str:
    """Duplica un número en letras entre paréntesis, como es costumbre en un
    pagaré/contrato ("4 (cuatro) cuotas"), para que no pueda alterarse a mano
    después de firmado.

    Solo cubre los valores que aparecen en las cláusulas (_TERM_* arriba); un
    número fuera de la tabla se devuelve en dígitos en vez de reventar, porque
    esto renderiza un documento y una excepción acá dejaría al operador sin
    poder imprimir. Si se agrega una condición con un número nuevo, sumarlo a
    la tabla -- el test de condiciones legales lo verifica."""
    return _NUMEROS_EN_LETRAS.get(valor, str(valor))


_ESTADOS_LABEL = {
    "PENDING": "Pendiente",
    "APPROVED": "Aprobado",
    "ACTIVE": "Activo",
    "PAID": "Pagado",
    "DEFAULTED": "Incumplido",
    # Ver la nota equivalente en loans_view.py: LoanStatusEnum.EXPIRED se
    # presenta como "Rechazado" en toda la UI y en los documentos.
    "EXPIRED": "Rechazado",
}

# Nota histórica: acá vivía _DRAFT_BANNER ("BORRADOR -- TEXTO LEGAL PENDIENTE DE
# REVISIÓN"), estampado en la Liquidación, el Pagaré y el Contrato. Se quitó al
# autorizarse el texto legal y cargarse las condiciones reales (_TERM_* arriba).
# Si alguna cláusula vuelve a quedar sin definir, corresponde reponerlo antes de
# entregar el documento a un cliente, no dejarlo salir con un dato en blanco.


def friendly_file_error(exc: OSError) -> str:
    """Translates a file I/O failure from saving/printing a generated document
    (locked file, no permissions, disk full) into a user-facing message --
    never a raw traceback, matching the rule ES-003 §5 already enforces
    elsewhere.

    Lives here rather than in a single view because every document-export path
    needs it: loans_view.py's four loan documents and dashboard_view.py's
    period report."""
    if isinstance(exc, PermissionError):
        return (
            "No se pudo guardar el documento: el archivo está abierto en otro "
            "programa o no tiene permisos de escritura en esa carpeta."
        )
    return f"No se pudo guardar el documento: {exc.strerror or exc}"


def _header(title: str) -> str:
    """Logo + legal-entity block + centered document title, per the layout in
    HeaderDocumentos.png (logo left, company/RUC/address right, divider,
    bold centered title below) -- supersedes the earlier solid-navy banner."""
    return f"""
    <table width="100%" cellspacing="0" cellpadding="0">
      <tr>
        <td width="130" valign="middle"><img src="{assets.logo_full_data_uri()}" width="110"/></td>
        <td valign="middle">
          <div style="font-size: 15px; font-weight: 700; color: {theme.TEXT_PRIMARY};">
            {_COMPANY_NAME} &mdash; RUC: {_COMPANY_RUC}
          </div>
          <div style="font-size: 12px; color: {theme.TEXT_MUTED};">{_COMPANY_ADDRESS}</div>
          <div style="font-size: 12px; color: {theme.TEXT_MUTED};">Cel: {_COMPANY_PHONE}</div>
        </td>
      </tr>
    </table>
    <hr style="border: none; border-top: 1px solid {theme.BORDER}; margin: 10px 0 14px 0;"/>
    <div style="text-align:center; font-size: 17px; font-weight: 700; text-transform: uppercase;
                color: {theme.PRIMARY}; margin-bottom: 16px;">
      {title}
    </div>
    """


def _client_block(client) -> str:
    return f"""
    <p><b>Cliente:</b> {client.first_name} {client.last_name}<br/>
    <b>Documento:</b> {client.national_id}<br/>
    <b>Direcci&oacute;n:</b> {client.address}<br/>
    <b>Tel&eacute;fono:</b> {client.phone_number}</p>
    """


def _footer(text: str) -> str:
    return f'<p style="margin-top:24px; font-size:12px; color:{theme.TEXT_MUTED};">{text}</p>'


def _cargos_y_garantia_block(loan) -> str:
    """BR-LOAN-005/006: desglose informativo de garant&iacute;a y cargos/seguros,
    si el pr&eacute;stamo tiene alguno cargado. No afecta el cronograma."""
    filas_cargos = [
        (nombre, monto)
        for nombre, monto in (
            ("Impuesto al inter&eacute;s", loan.charge_interest_tax),
            ("Gastos administrativos", loan.charge_admin_fee),
            (
                "Seguro de cancelaci&oacute;n de deuda",
                loan.charge_cancellation_insurance,
            ),
            ("Seguros contratados", loan.charge_contracted_insurance),
        )
        if monto
    ]

    cargos_seccion = ""
    if filas_cargos:
        filas_html = "".join(
            f"<tr><td>{nombre}</td><td>{gs(monto)}</td></tr>"
            for nombre, monto in filas_cargos
        )
        cargos_seccion = f"""
        <h3 style="color:{theme.PRIMARY};">Cargos y seguros</h3>
        <table border="1" cellspacing="0" cellpadding="6" width="100%">
          <tr style="background-color:{theme.APP_BACKGROUND};">
            <th>Concepto</th><th>Monto (Gs)</th>
          </tr>
          {filas_html}
          <tr><td><b>Total cargos</b></td><td><b>{gs(loan.total_charges)}</b></td></tr>
        </table>
        """

    garantia_seccion = ""
    if loan.guarantee_type:
        garantia_seccion = f"""
        <p><b>Garant&iacute;a:</b> {loan.guarantee_type} &mdash; Monto aplicado:
        {gs(loan.guarantee_amount)}<br/>
        <b>Total del cr&eacute;dito (incl. cargos):</b> {gs(loan.total_credit_with_charges)}</p>
        """

    return cargos_seccion + garantia_seccion


def liquidacion_html(loan, client, schedule) -> str:
    """loan: loan_service_pb2.GetLoanByIdResponse
    client: client_service_pb2.GetClientByIdResponse
    schedule: loan_service_pb2.GetAmortizationScheduleResponse"""
    estado = _ESTADOS_LABEL.get(loan.status, loan.status)
    filas = "".join(
        f"<tr><td>{i.installment_number}</td><td>{gs(i.payment_amount)}</td>"
        f"<td>{gs(i.principal_portion)}</td><td>{gs(i.interest_portion)}</td>"
        f"<td>{gs(i.remaining_balance)}</td></tr>"
        for i in schedule.installments
    )
    return f"""
    <html><body style="font-family: sans-serif; color: {theme.TEXT_PRIMARY};">
    {_header("Liquidaci&oacute;n de Pr&eacute;stamo")}
    {_client_block(client)}
    <p><b>Pr&eacute;stamo:</b> {loan.id}<br/>
    <b>Estado:</b> {estado}<br/>
    <b>Capital:</b> {gs(loan.principal_amount)}<br/>
    <b>Tasa de inter&eacute;s:</b> {rate_percent(loan.interest_rate)} anual
    ({rate_percent_mensual(loan.interest_rate)} mensual sobre saldos deudores)<br/>
    <b>Plazo:</b> {loan.term_months} meses<br/>
    <b>Total pagado:</b> {gs(loan.total_paid)}<br/>
    <b>Saldo restante:</b> {gs(loan.remaining_balance)}</p>
    {_cargos_y_garantia_block(loan)}
    <h3 style="color:{theme.PRIMARY};">Cronograma de amortizaci&oacute;n</h3>
    <table border="1" cellspacing="0" cellpadding="6" width="100%">
      <tr style="background-color:{theme.APP_BACKGROUND};">
        <th>Cuota</th><th>Monto (Gs)</th><th>Capital (Gs)</th>
        <th>Inter&eacute;s (Gs)</th><th>Saldo (Gs)</th>
      </tr>
      {filas}
    </table>
    {_footer("Documento generado por el sistema de CREDIMED UME. V&aacute;lido &uacute;nicamente "
             "junto con la firma y sello de la entidad.")}
    </body></html>
    """


def _garantia_linea(loan) -> str:
    """Línea de codeudor/garantía para Pagaré y Contrato -- el modelo real
    (BR-LOAN-005) no distingue un codeudor de una garantía en general, así
    que se muestra tal cual está cargada, en blanco si no hay ninguna (el
    pagaré de referencia siempre deja esta línea presente, con o sin
    codeudor)."""
    if loan.guarantee_type:
        return (
            f"<b>Garant&iacute;a / Codeudor solidario:</b> {loan.guarantee_type} "
            f"&mdash; Monto: {gs(loan.guarantee_amount)}<br/>"
        )
    return (
        "<b>Garant&iacute;a / Codeudor solidario:</b> Sin garant&iacute;a "
        "registrada<br/>"
    )


def pagare_html(loan, client) -> str:
    return f"""
    <html><body style="font-family: sans-serif; color: {theme.TEXT_PRIMARY};">
    {_header("Pagar&eacute; a la Orden")}
    {_client_block(client)}
    <p>{_garantia_linea(loan)}</p>
    <p>DECLARO(AMOS) ADEUDAR a {_COMPANY_NAME} la suma de
    <b>Guaran&iacute;es {gs(loan.principal_amount)}</b>, que PAGAR&Eacute;(MOS)
    solidariamente, a su orden, libre de gastos y sin protesto, en
    <b>{loan.term_months}</b> cuotas iguales, mensuales y consecutivas, con
    vencimiento la primera de ellas el d&iacute;a <b>{fecha(loan.first_due_date)}</b>,
    y las siguientes cuotas en esas mismas fechas de los meses subsiguientes
    hasta su total cancelaci&oacute;n, en el domicilio de {_COMPANY_NAME},
    sito en {_COMPANY_ADDRESS}.</p>
    <p>Queda expresamente pactado que los importes de las cuotas
    documentadas en este instrumento devengar&aacute;n un inter&eacute;s
    compensatorio del <b>{rate_percent_mensual(loan.interest_rate)}
    mensual</b> sobre saldos deudores.</p>
    <p>En caso de mora se aplicar&aacute;, sobre cada cuota vencida e impaga,
    un inter&eacute;s moratorio en car&aacute;cter punitorio del
    <b>{_TERM_MORATORY_RATE}</b>, que se devengar&aacute; a partir de los
    <b>{_TERM_MORATORY_GRACE_DAYS} ({_numero_en_letras(_TERM_MORATORY_GRACE_DAYS)})
    d&iacute;as</b> corridos contados desde la fecha de su primer
    vencimiento.</p>
    <p>La falta de pago de
    <b>{_TERM_ACCELERATION_INSTALLMENTS}
    ({_numero_en_letras(_TERM_ACCELERATION_INSTALLMENTS)}) cuotas
    vencidas</b> facultar&aacute; a {_COMPANY_NAME} a exigir el total
    adeudado, inclusive las cuotas no vencidas, produci&eacute;ndose la mora
    por el mero vencimiento del plazo, sin necesidad de ning&uacute;n
    requerimiento judicial y/o extrajudicial.</p>
    <p>Todas las partes intervinientes en este documento se someten a la
    jurisdicci&oacute;n y competencia de los Jueces y Tribunales de
    <b>{_TERM_JURISDICTION_CITY}</b>.</p>
    <p><b>Pr&eacute;stamo:</b> {loan.id}</p>
    <p style="margin-top:48px;">Firma del deudor: ______________________________</p>
    {_footer("Lugar y fecha: ______________________________")}
    </body></html>
    """


def cronograma_html(loan, client, schedule) -> str:
    """Cronograma de pago standalone, pensado para entregarse como copia
    impresa/f&iacute;sica al cliente (a diferencia de la Liquidaci&oacute;n,
    que es un resumen completo del pr&eacute;stamo para uso interno). Incluye
    el nombre del cliente y el asesor que registr&oacute; el pr&eacute;stamo
    para que el cliente sepa a qui&eacute;n dirigirse -- por nombre y C.I.
    (BR-AUTH-006), cayendo de vuelta a su usuario del sistema
    (created_by_username) cuando ese operador no tiene datos personales
    cargados, y a "No registrado" cuando no se conoce (pr&eacute;stamos
    previos a created_by_user_id).

    loan: loan_service_pb2.GetLoanByIdResponse
    client: client_service_pb2.GetClientByIdResponse
    schedule: loan_service_pb2.GetAmortizationScheduleResponse"""
    asesor = responsable(
        loan.created_by_full_name,
        loan.created_by_national_id,
        respaldo=loan.created_by_username,
    )
    filas = "".join(
        f"<tr><td>{i.installment_number}</td><td>{fecha(i.due_date)}</td>"
        f"<td>{gs(i.payment_amount)}</td><td>{gs(i.principal_portion)}</td>"
        f"<td>{gs(i.interest_portion)}</td><td>{gs(i.remaining_balance)}</td></tr>"
        for i in schedule.installments
    )
    return f"""
    <html><body style="font-family: sans-serif; color: {theme.TEXT_PRIMARY};">
    {_header("Cronograma de Pago")}
    {_client_block(client)}
    <p><b>Asesor responsable:</b> {asesor}<br/>
    <b>Pr&eacute;stamo:</b> {loan.id}<br/>
    <b>Capital:</b> {gs(loan.principal_amount)}<br/>
    <b>Tasa de inter&eacute;s:</b> {rate_percent(loan.interest_rate)}<br/>
    <b>Plazo:</b> {loan.term_months} meses<br/>
    <b>Primer vencimiento:</b> {fecha(loan.first_due_date)}</p>
    <table border="1" cellspacing="0" cellpadding="6" width="100%">
      <tr style="background-color:{theme.APP_BACKGROUND};">
        <th>Cuota</th><th>Vencimiento</th><th>Monto (Gs)</th>
        <th>Capital (Gs)</th><th>Inter&eacute;s (Gs)</th><th>Saldo (Gs)</th>
      </tr>
      {filas}
    </table>
    {_footer("Este cronograma es informativo y est&aacute; sujeto a los t&eacute;rminos "
             "y condiciones establecidos en el Pagar&eacute; y el Contrato de "
             "Pr&eacute;stamo firmados. Copia entregada al cliente.")}
    </body></html>
    """


def responsable(nombre: str, national_id: str, respaldo: str = "") -> str:
    """Texto de un operador responsable para los documentos: "Nombre Apellido
    (C.I. 1234567)" (BR-AUTH-006).

    Degrada por partes en vez de todo o nada, porque los usuarios anteriores a
    BR-AUTH-006 no tienen datos personales cargados: sin C.I. imprime solo el
    nombre, y sin nombre cae a `respaldo` (el username), que es mejor que
    dejar el campo vacío en un papel que se le entrega al cliente. Sin nada,
    "No registrado".

    Compartido por el Cronograma de Pago ("Asesor responsable") y el
    Comprobante de Pago ("Registrado por") -- las dos referencias a un
    operador que pidió el usuario.
    """
    etiqueta = nombre or respaldo
    if not etiqueta:
        return "No registrado"
    if national_id:
        return f"{etiqueta} (C.I. {national_id})"
    return etiqueta


def cuotas_cubiertas_texto(numeros, total: int) -> str:
    """ "Cuota(s) 1 de 18" / "Cuota(s) 1,2 de 18" -- el formato exacto pedido
    para el Comprobante de Pago (BR-LOAN-011).

    `numeros` es RecordPaymentResponse.covered_installments: los números de
    cuota que el pago cubrió según la imputación real del servidor. Puede
    traer más de uno cuando un pago salda el resto de una cuota y parte de la
    siguiente.
    """
    if not numeros:
        # No debería pasar con un pago real (siempre se imputa a alguna
        # cuota), pero el comprobante no es lugar para reventar por eso.
        return f"Cuota(s) — de {total}"
    return f"Cuota(s) {','.join(str(n) for n in numeros)} de {total}"


def comprobante_pago_html(loan, client, payment) -> str:
    """Comprobante de Pago -- BR-LOAN-011. Se emite despu&eacute;s de
    registrar un pago, para entregar o enviar al deudor como constancia.

    Sin banner de borrador: no tiene texto legal a revisar, solo el detalle de
    un pago ya registrado (mismo criterio que el Cronograma y el Reporte de
    cierre de per&iacute;odo).

    loan: loan_service_pb2.GetLoanByIdResponse
    client: client_service_pb2.GetClientByIdResponse
    payment: loan_service_pb2.RecordPaymentResponse"""
    cuotas = cuotas_cubiertas_texto(
        payment.covered_installments, payment.total_installments
    )
    registrado_por = responsable(
        payment.recorded_by_name, payment.recorded_by_national_id
    )
    fecha_pago = payment.paid_at.ToDatetime().strftime("%d/%m/%Y %H:%M")
    saldado = payment.status == "PAID"
    cierre = (
        '<p style="font-weight:700; color:%s;">Con este pago el pr&eacute;stamo '
        "queda totalmente cancelado.</p>" % theme.SUCCESS
        if saldado
        else ""
    )
    return f"""
    <html><body style="font-family: sans-serif; color: {theme.TEXT_PRIMARY};">
    {_header("Comprobante de Pago")}
    {_client_block(client)}
    <p><b>Pr&eacute;stamo:</b> {loan.id}</p>
    <table border="1" cellspacing="0" cellpadding="8" width="100%">
      <tr style="background-color:{theme.PRIMARY}; color:white;">
        <th align="left">Concepto</th><th align="right">Detalle</th>
      </tr>
      <tr><td>Monto abonado</td>
          <td align="right"><b>{gs(payment.amount_paid)}</b></td></tr>
      <tr><td>Cuota(s) abonada(s)</td><td align="right">{cuotas}</td></tr>
      <tr><td>Fecha y hora del pago</td><td align="right">{fecha_pago}</td></tr>
      <tr><td>Referencia de transferencia</td>
          <td align="right">{payment.transfer_reference}</td></tr>
      <tr><td>Total pagado del pr&eacute;stamo</td>
          <td align="right">{gs(payment.total_paid)}</td></tr>
      <tr><td>Saldo restante</td>
          <td align="right"><b>{gs(payment.remaining_balance)}</b></td></tr>
    </table>
    {cierre}
    <p style="margin-top:16px;"><b>Registrado por:</b> {registrado_por}</p>
    {_footer("Comprobante emitido por el sistema de CREDIMED UME. El saldo "
             "restante puede variar por cargos o ajustes posteriores a la "
             "fecha de emisi&oacute;n de este comprobante.")}
    </body></html>
    """


def _filas_reporte(report) -> list[tuple[str, str, str]]:
    """(sección, concepto, valor) del reporte de cierre de período
    (BR-DASH-002), compartido por reporte_periodo_html() y por la tabla de la
    vista (dashboard_view.py) -- una sola definición del contenido del
    reporte, para que el PDF y la pantalla no se desincronicen.

    report: dashboard_service_pb2.GetPeriodReportResponse"""
    return [
        (
            "Movimiento del período",
            "Clientes registrados",
            str(report.clients_registered),
        ),
        ("Movimiento del período", "Préstamos solicitados", str(report.loans_created)),
        (
            "Movimiento del período",
            "Capital solicitado",
            gs(report.principal_created),
        ),
        ("Movimiento del período", "Préstamos aprobados", str(report.loans_approved)),
        (
            "Movimiento del período",
            "Capital aprobado",
            gs(report.principal_approved),
        ),
        ("Cobranza del período", "Pagos recibidos", str(report.payments_count)),
        ("Cobranza del período", "Total cobrado", gs(report.payments_total)),
        ("Cobranza del período", "Préstamos cancelados", str(report.loans_paid)),
        ("Situación al cierre", "Préstamos activos", str(report.active_loans_at_close)),
        (
            "Situación al cierre",
            "Saldo pendiente de cobro",
            gs(report.outstanding_at_close),
        ),
        ("Situación al cierre", "Monto total de mora", gs(report.overdue_at_close)),
        (
            "Situación al cierre",
            "Préstamos en mora",
            str(report.overdue_loans_at_close),
        ),
    ]


def reporte_periodo_html(report, generated_by: str = "") -> str:
    """BR-DASH-002: reporte de cierre de período, para imprimir o archivar una
    vez cerrado el mes/trimestre/año.

    No lleva _DRAFT_BANNER: igual que el Cronograma de Pago, no tiene texto
    legal que revisar -- son cifras calculadas sobre datos ya registrados.

    report: dashboard_service_pb2.GetPeriodReportResponse"""
    filas = ""
    seccion_actual = ""
    for seccion, concepto, valor in _filas_reporte(report):
        if seccion != seccion_actual:
            seccion_actual = seccion
            filas += (
                f'<tr style="background-color:{theme.APP_BACKGROUND};">'
                f'<td colspan="2"><b>{seccion}</b></td></tr>'
            )
        filas += f'<tr><td>{concepto}</td><td align="right">{valor}</td></tr>'

    asesor = f"<br/><b>Generado por:</b> {generated_by}" if generated_by else ""
    return f"""
    <html><body style="font-family: sans-serif; color: {theme.TEXT_PRIMARY};">
    {_header("Reporte de Cierre de Per&iacute;odo")}
    <p><b>Per&iacute;odo:</b> del {fecha(report.start_date)} al
    {fecha(report.end_date)}{asesor}</p>
    <table border="1" cellspacing="0" cellpadding="6" width="100%">
      <tr style="background-color:{theme.PRIMARY}; color:white;">
        <th align="left">Concepto</th><th align="right">Valor</th>
      </tr>
      {filas}
    </table>
    <p style="font-size:12px; color:{theme.TEXT_MUTED}; margin-top:16px;">
    "Movimiento" y "Cobranza" miden lo ocurrido dentro del per&iacute;odo
    seleccionado. "Situaci&oacute;n al cierre" es una foto del estado actual
    de la cartera al momento de generar este reporte, no del &uacute;ltimo
    d&iacute;a del per&iacute;odo.</p>
    {_footer("Documento generado por el sistema de CREDIMED UME. Uso interno.")}
    </body></html>
    """


def contrato_html(loan, client) -> str:
    return f"""
    <html><body style="font-family: sans-serif; color: {theme.TEXT_PRIMARY};">
    {_header("Contrato de Pr&eacute;stamo")}
    <p>Entre {_COMPANY_NAME}, con RUC {_COMPANY_RUC}, con domicilio en
    {_COMPANY_ADDRESS}, en adelante {_COMPANY_NAME} o LA ENTIDAD, por una
    parte; y por la otra el(la/los) Sr(a)(es). abajo identificado(s), en
    adelante EL(LOS) PRESTATARIO(S), convienen en celebrar el presente
    Contrato de Pr&eacute;stamo de Dinero, sujeto a las cl&aacute;usulas y
    condiciones siguientes.</p>
    {_client_block(client)}
    <p>{_garantia_linea(loan)}</p>
    <h3 style="color:{theme.PRIMARY};">Cl&aacute;usulas</h3>
    <p><b>Primera (Objeto):</b> {_COMPANY_NAME} otorga al(los) Prestatario(s)
    un pr&eacute;stamo de dinero por la suma de
    <b>Guaran&iacute;es {gs(loan.principal_amount)}</b>, que se desembolsa a
    la firma del presente instrumento, conjuntamente con un Pagar&eacute; a
    la orden por dicho monto, destinado a servir como t&iacute;tulo de
    cr&eacute;dito.</p>
    <p><b>Segunda (Reembolso):</b> El(Los) Prestatario(s) se compromete(n) a
    reembolsar el pr&eacute;stamo otorgado en <b>{loan.term_months}</b>
    cuotas iguales, mensuales y consecutivas, bajo el sistema de
    amortizaci&oacute;n franc&eacute;s (cuota fija), venciendo la primera de
    ellas el d&iacute;a <b>{fecha(loan.first_due_date)}</b>, mediante transferencia
    bancaria, d&eacute;bito directo o descuento en cuenta, seg&uacute;n lo
    acordado.</p>
    <p><b>Tercera (Intereses):</b> Se acuerda el pago de un inter&eacute;s
    compensatorio del <b>{rate_percent_mensual(loan.interest_rate)}
    mensual</b> sobre saldos deudores, abonado junto con las cuotas de
    amortizaci&oacute;n del capital. Para el caso de falta de pago en la
    fecha convenida, se aplicar&aacute; adem&aacute;s, sobre cada cuota
    vencida e impaga, un inter&eacute;s moratorio en car&aacute;cter
    punitorio del <b>{_TERM_MORATORY_RATE}</b>, que se devengar&aacute; a
    partir de los <b>{_TERM_MORATORY_GRACE_DAYS}
    ({_numero_en_letras(_TERM_MORATORY_GRACE_DAYS)}) d&iacute;as</b> corridos
    contados desde la fecha de su primer vencimiento.</p>
    <p><b>Cuarta (Mora y vencimiento anticipado):</b> La mora se
    producir&aacute; por el mero vencimiento de los plazos, sin necesidad de
    interpelaci&oacute;n judicial alguna. La falta de pago de
    <b>{_TERM_ACCELERATION_INSTALLMENTS}
    ({_numero_en_letras(_TERM_ACCELERATION_INSTALLMENTS)}) cuotas
    vencidas</b> har&aacute; decaer de pleno derecho todos los plazos
    estipulados para el pago, facultando a {_COMPANY_NAME} a declarar
    vencidas todas las cuotas y exigir el pago de la totalidad de la deuda,
    ejecutando para el efecto el Pagar&eacute; suscripto junto con este
    contrato.</p>
    <p><b>Quinta (Central de riesgo crediticio):</b> El(Los) Prestatario(s)
    autoriza(n) expresamente a {_COMPANY_NAME} para que, por su cuenta o a
    trav&eacute;s de terceros, recabe informaci&oacute;n sobre su
    situaci&oacute;n patrimonial y crediticia, y para que, en caso de atraso
    en el pago, informe sus datos a las centrales de riesgo crediticio
    correspondientes, conforme a la legislaci&oacute;n vigente.</p>
    <p><b>Sexta (Jurisdicci&oacute;n):</b> Todas las partes intervinientes en
    este contrato se someten a la jurisdicci&oacute;n y competencia de los
    Jueces y Tribunales de <b>{_TERM_JURISDICTION_CITY}</b>.</p>
    <p><b>Pr&eacute;stamo:</b> {loan.id}</p>
    <p style="margin-top:48px;">Firma del deudor: ______________________________</p>
    {_footer("Firma de " + _COMPANY_NAME + ": ______________________________")}
    </body></html>
    """
