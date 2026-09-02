"""HTML templates for the loan documents (Liquidación de Préstamo, Pagaré,
Contrato, Cronograma de Pago, Comprobante de Pago), rendered client-side from
data already available via existing RPCs
(GetLoanById/GetClientById/GetAmortizationSchedule) -- no server changes
needed for the first three; the fourth needed Loan.created_by_username (see
loan_service.proto).

Also holds the two dashboard reports, which are not loan documents but share
the same header/footer and export path: reporte_periodo_html (BR-DASH-002) and
reporte_estado_pagos_html (BR-DASH-003).

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

from decimal import Decimal, InvalidOperation

from cas_client import assets, theme
from cas_client.formatting import (
    fecha,
    fecha_hora,
    gs,
    rate_percent,
    rate_percent_mensual,
)

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
# cada cuota vencida (no hay un segundo interés separado sobre el capital:
# la entidad definió una sola tasa). Por eso hay un único _TERM_MORATORY_RATE
# donde antes había un par moratoria/punitoria.
_TERM_MORATORY_RATE = "0,38% mensual"
# Revisados por la entidad el 2026-09-02 (antes 11 y 4, respectivamente) --
# ver docs/"modelo de pagare.pdf" y docs/"contrato modificado credi ume.docx",
# las dos referencias que la entidad marcó a mano con los valores nuevos.
_TERM_MORATORY_GRACE_DAYS = 5
_TERM_ACCELERATION_INSTALLMENTS = 3
_TERM_JURISDICTION_CITY = "Coronel Oviedo"

# El interés compensatorio NO se escribe fijo acá: sale de loan.interest_rate,
# para que el documento no pueda contradecir la cuota que el sistema realmente
# calculó. Con la tasa fija vigente (config.LOAN_FIXED_INTEREST_RATE = 0.20,
# el tope legal) rate_percent_mensual() imprime "1,667%", la tasa mensual
# autorizada -- ya no "3,75%": ese número mezclaba interés y gastos
# administrativos en una sola cifra (ver _cargos_y_garantia_block más abajo,
# que es donde el crédito financiado con cargos se declara por separado).
#
# BR-LOAN-013: ese porcentaje se aplica al **monto original del préstamo**, no
# al saldo deudor -- por eso el interés de cada cuota es el mismo y las cuotas
# son iguales. Las cláusulas de abajo tienen que decirlo así: "sobre saldos
# deudores" describiría un cálculo distinto (y más barato) del que el
# cronograma adjunto realmente hace.

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


# BR-LOAN-006: los 4 cargos con su nombre para los documentos. Espeja
# _CHARGE_FIELDS de loans_view.py -- se mantiene a mano igual que
# _ESTADOS_LABEL, porque este m&oacute;dulo no importa vistas (documents_docx.py
# reutiliza esta constante para que el PDF y el DOCX no puedan diferir).
_CHARGE_LABELS = (
    ("charge_interest_tax", "Impuesto s/ intereses"),
    ("charge_admin_fee", "Gastos administrativos por desembolso"),
    ("charge_cancellation_insurance", "Seguro de cancelaci&oacute;n de deuda"),
    ("charge_contracted_insurance", "Seguros contratados"),
)


def filas_composicion_credito(loan) -> list[tuple[str, str, bool]]:
    """(concepto, monto, destacar) de la composici&oacute;n del cr&eacute;dito.

    Una sola definici&oacute;n para el PDF y el DOCX, por el mismo motivo que
    `_filas_reporte`: son cifras que el cliente compara entre el papel que
    firma y el que se lleva.

    Es tambi&eacute;n donde queda dicho, en el documento y no solo en la
    pantalla, que el cliente recibe el capital pero amortiza capital + cargos
    (BR-LOAN-006): sin estas filas el cronograma adjunto arranca de un monto
    mayor al del pr&eacute;stamo sin ninguna explicaci&oacute;n visible.
    """
    filas = [("Capital solicitado", gs(loan.principal_amount), False)]
    filas += [
        (nombre, gs(getattr(loan, campo)), False)
        for campo, nombre in _CHARGE_LABELS
        if getattr(loan, campo)
    ]
    filas += [
        ("Total de cargos financiados", gs(loan.total_charges), False),
        (
            "Total del cr&eacute;dito (monto que amortizan las cuotas)",
            gs(loan.total_credit_with_charges),
            True,
        ),
        ("Total de inter&eacute;s", gs(loan.total_interest), False),
        ("Total a pagar", gs(loan.total_to_pay), True),
        ("Cuota mensual", gs(loan.installment_amount), True),
        ("Importe a desembolsar al cliente", gs(loan.amount_to_disburse), False),
    ]
    return filas


def _cargos_y_garantia_block(loan) -> str:
    """BR-LOAN-005/006: composici&oacute;n del cr&eacute;dito (capital + cargos
    capitalizados) y garant&iacute;a de respaldo."""
    filas_html = "".join(
        f"<tr><td>{'<b>' if destacar else ''}{nombre}{'</b>' if destacar else ''}</td>"
        f"<td>{'<b>' if destacar else ''}{monto}{'</b>' if destacar else ''}</td></tr>"
        for nombre, monto, destacar in filas_composicion_credito(loan)
    )
    composicion = f"""
        <h3 style="color:{theme.PRIMARY};">Composici&oacute;n del cr&eacute;dito</h3>
        <table border="1" cellspacing="0" cellpadding="6" width="100%">
          <tr style="background-color:{theme.APP_BACKGROUND};">
            <th>Concepto</th><th>Monto (Gs)</th>
          </tr>
          {filas_html}
        </table>
        <p style="font-size:12px; color:{theme.TEXT_MUTED};">Los cargos y
        seguros se financian junto con el capital: integran el total del
        cr&eacute;dito, que es el monto que amortizan las cuotas y sobre el
        que se calcula el inter&eacute;s. El importe entregado al cliente es
        el capital solicitado.</p>
        """

    garantia_seccion = ""
    if loan.guarantee_type:
        garantia_seccion = f"""
        <p><b>Garant&iacute;a:</b> {loan.guarantee_type} &mdash; Monto aplicado:
        {gs(loan.guarantee_amount)}</p>
        """

    return composicion + garantia_seccion


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
    <b>Capital solicitado:</b> {gs(loan.principal_amount)}<br/>
    <b>Total del cr&eacute;dito (capital + cargos):</b>
    {gs(loan.total_credit_with_charges)}<br/>
    <b>Tasa de inter&eacute;s:</b> {rate_percent(loan.interest_rate)} anual
    ({rate_percent_mensual(loan.interest_rate)} mensual sobre el monto
    original)<br/>
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


def tiene_cargos_financiados(loan) -> bool:
    """True si el préstamo realmente capitaliza algún cargo (BR-LOAN-006).

    Compartido por el Contrato y el Pagaré: con cargos en cero, decir "se
    adicionan 0 Gs ... el monto total del crédito asciende a Guaraníes
    <el mismo capital>" es ruido -- la referencia que la entidad devolvió
    (docs/"contrato modificado credi ume.docx") borró esa frase a mano
    precisamente en un préstamo sin cargos. Con cargos, la frase sigue
    siendo obligatoria: omitirla declararía una deuda menor a la que cobra
    el cronograma adjunto."""
    try:
        return Decimal(loan.total_charges or "0") > 0
    except InvalidOperation:
        return False


def _clausula_objeto_texto(loan) -> str:
    """Cuerpo de la cláusula Primera (Objeto) del Contrato, a partir de
    "que se desembolsa a la firma del presente instrumento" -- la parte que
    cambia según si el préstamo tiene cargos capitalizados o no."""
    if tiene_cargos_financiados(loan):
        return (
            f"Al capital se adicionan <b>{gs(loan.total_charges)}</b> en "
            "concepto de cargos, gastos administrativos y seguros, que se "
            "financian junto con &eacute;l, de modo que el monto total del "
            "cr&eacute;dito asciende a <b>Guaran&iacute;es "
            f"{gs(loan.total_credit_with_charges)}</b>, importe por el cual "
            "se suscribe un Pagar&eacute; a la orden destinado a servir "
            "como t&iacute;tulo de cr&eacute;dito."
        )
    return (
        "Importe por el cual se suscribe un Pagar&eacute; a la orden "
        "destinado a servir como t&iacute;tulo de cr&eacute;dito."
    )


def clausula_objeto_texto_plano(loan) -> str:
    """Igual que `_clausula_objeto_texto()` pero sin entidades HTML ni
    `<b>`, para el DOCX (`documents_docx.py`'s `contrato_docx`), que arma sus
    propios runs en texto plano."""
    if tiene_cargos_financiados(loan):
        return (
            f"Al capital se adicionan {gs(loan.total_charges)} en concepto "
            "de cargos, gastos administrativos y seguros, que se financian "
            "junto con él, de modo que el monto total del crédito asciende "
            f"a Guaraníes {gs(loan.total_credit_with_charges)}, importe por "
            "el cual se suscribe un Pagaré a la orden destinado a servir "
            "como título de crédito."
        )
    return (
        "Importe por el cual se suscribe un Pagaré a la orden destinado a "
        "servir como título de crédito."
    )


def pagare_integracion_texto(loan) -> str:
    """Frase que desglosa capital + cargos dentro de la declaración de deuda
    del Pagaré -- solo aparece cuando el préstamo realmente capitaliza
    cargos (BR-LOAN-006); vacía en caso contrario, mismo criterio que
    `_clausula_objeto_texto()`. Sin acentos ni entidades propias, así que
    sirve tal cual tanto para el HTML como para el DOCX."""
    if tiene_cargos_financiados(loan):
        return (
            f", integrada por un capital de {gs(loan.principal_amount)} y "
            f"{gs(loan.total_charges)} en concepto de cargos y seguros "
            "financiados,"
        )
    return ""


def _garantia_label_valor(loan) -> tuple[str, str]:
    """(etiqueta, valor) de la garant&iacute;a/codeudor -- el modelo real
    (BR-LOAN-005) no distingue un codeudor de una garant&iacute;a en
    general, as&iacute; que se muestra tal cual est&aacute; cargada, o "Sin
    garant&iacute;a registrada" si no hay ninguna (el pagar&eacute; de
    referencia siempre deja esta l&iacute;nea presente, con o sin
    codeudor). Compartido por la cl&aacute;usula del Contrato y la ficha de
    identificaci&oacute;n del Pagar&eacute;."""
    etiqueta = "Garant&iacute;a / Codeudor solidario"
    if loan.guarantee_type:
        return (
            etiqueta,
            f"{loan.guarantee_type} &mdash; Monto: {gs(loan.guarantee_amount)}",
        )
    return etiqueta, "Sin garant&iacute;a registrada"


def _garantia_linea(loan) -> str:
    etiqueta, valor = _garantia_label_valor(loan)
    return f"<b>{etiqueta}:</b> {valor}<br/>"


def _pagare_header(loan) -> str:
    """Encabezado del Pagar&eacute;, sin logo ni bloque de identidad de la
    entidad: imita directamente el layout de un pagar&eacute; real
    (docs/"modelo de pagare.pdf") -- t&iacute;tulo en may&uacute;scula a la
    izquierda, referencia del pr&eacute;stamo a la derecha. El nombre y el
    domicilio de {_COMPANY_NAME} ya se declaran dentro del propio texto del
    pagar&eacute;, igual que en el documento de referencia (que tampoco
    lleva membrete), as&iacute; que no hace falta repetirlos acá."""
    return f"""
    <table width="100%" cellspacing="0" cellpadding="0">
      <tr>
        <td valign="bottom">
          <div style="font-size: 20px; font-weight: 700; text-transform: uppercase;
                      color: {theme.PRIMARY};">
            Pagar&eacute; a la Orden
          </div>
        </td>
        <td align="right" valign="bottom">
          <div style="font-size: 11px; color: {theme.TEXT_MUTED};">
            Pr&eacute;stamo N&deg; {loan.id}
          </div>
        </td>
      </tr>
    </table>
    <hr style="border: none; border-top: 1px solid {theme.BORDER}; margin: 8px 0 18px 0;"/>
    """


def pagare_html(loan, client) -> str:
    garantia_etiqueta, garantia_valor = _garantia_label_valor(loan)
    return f"""
    <html><body style="font-family: sans-serif; color: {theme.TEXT_PRIMARY};">
    {_pagare_header(loan)}
    <p>DECLARO(AMOS) ADEUDAR A {_COMPANY_NAME} la suma de
    <b>Guaran&iacute;es {gs(loan.total_credit_with_charges)}</b>{pagare_integracion_texto(loan)}
    que PAGAR&Eacute;(MOS) solidariamente, a su orden, libre de gastos y sin
    protesto, en <b>{loan.term_months}</b> cuotas iguales, mensuales y
    consecutivas, con vencimiento la primera de ellas el d&iacute;a
    <b>{fecha(loan.first_due_date)}</b>, y las siguientes cuotas en esas
    mismas fechas de los meses subsiguientes, que ser&aacute;n abonadas
    junto con los intereses compensatorios, calculados mensualmente sobre
    el monto original del pr&eacute;stamo, hasta su total
    cancelaci&oacute;n, en el domicilio de {_COMPANY_NAME}, sito en
    {_COMPANY_ADDRESS}.</p>
    <p>Queda expresamente pactado que los importes de las cuotas
    documentadas en este instrumento devengar&aacute;n un inter&eacute;s
    compensatorio del <b>{rate_percent_mensual(loan.interest_rate)}
    mensual</b>, calculado sobre el monto original del pr&eacute;stamo. En
    caso de mora se aplicar&aacute;, sobre cada cuota vencida e impaga, un
    inter&eacute;s moratorio en car&aacute;cter punitorio del
    <b>{_TERM_MORATORY_RATE}</b>, que se devengar&aacute; a partir de los
    <b>{_TERM_MORATORY_GRACE_DAYS} ({_numero_en_letras(_TERM_MORATORY_GRACE_DAYS)})
    d&iacute;as</b> corridos contados desde la fecha de su primer
    vencimiento y durante el per&iacute;odo de cobro hasta la
    restituci&oacute;n de la deuda declarada impaga.</p>
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
    <p style="margin-top:20px;">{_TERM_JURISDICTION_CITY}, ____ de
    ________________ de ________</p>
    <table cellspacing="0" cellpadding="3" style="margin-top:12px;">
      <tr><td><b>Cr&eacute;dito No.</b></td><td>&nbsp;&nbsp;{loan.id}</td></tr>
      <tr><td><b>Nombre</b></td>
          <td>&nbsp;&nbsp;{client.first_name} {client.last_name}</td></tr>
      <tr><td><b>Domicilio</b></td><td>&nbsp;&nbsp;{client.address}</td></tr>
      <tr><td><b>C.I. No.</b></td><td>&nbsp;&nbsp;{client.national_id}</td></tr>
      <tr><td><b>{garantia_etiqueta}</b></td><td>&nbsp;&nbsp;{garantia_valor}</td></tr>
    </table>
    {_footer("Firma: ______________________________")}
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


_MEDIOS_DE_PAGO_LABEL = {
    "EFECTIVO": "Efectivo (caja)",
    "TRANSFERENCIA": "Transferencia / descuento",
}


def filas_medio_de_pago(payment) -> list[tuple[str, str]]:
    """BR-CAJA-004: (concepto, detalle) de c&oacute;mo se cobr&oacute; el pago,
    para el Comprobante de Pago.

    La fila de referencia se omite en efectivo en vez de imprimirse vac&iacute;a:
    ah&iacute; no hay n&uacute;mero de transferencia que mostrar, y una l&iacute;nea
    "Referencia: " en blanco en un comprobante que se le entrega al deudor
    parece un dato que se perdi&oacute;. Compartido por el HTML y el DOCX --
    misma raz&oacute;n que _filas_reporte(): una sola definici&oacute;n para
    que los dos formatos no se desincronicen.

    payment: loan_service_pb2.RecordPaymentResponse"""
    # Vac&iacute;o = un servidor anterior a BR-CAJA-004, donde todo pago era
    # por transferencia -- mismo criterio por defecto que usa el servidor.
    medio = payment.payment_method or "TRANSFERENCIA"
    filas = [("Medio de pago", _MEDIOS_DE_PAGO_LABEL.get(medio, medio))]
    if medio != "EFECTIVO":
        filas.append(("Referencia de transferencia", payment.transfer_reference))
    return filas


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
    # fecha_hora() y no un strftime propio: paid_at llega como un naive UTC
    # (Timestamp.ToDatetime()), y un comprobante que el deudor se lleva impreso
    # tiene que decir la hora a la que pagó, no su equivalente en UTC.
    fecha_pago = fecha_hora(payment.paid_at.ToDatetime())
    medio_filas = "".join(
        f'<tr><td>{concepto}</td><td align="right">{detalle}</td></tr>'
        for concepto, detalle in filas_medio_de_pago(payment)
    )
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
      {medio_filas}
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


def _relacion_cuota_ingreso(loan, client) -> tuple[str, bool]:
    """(texto, excede_tope) -- la cuota mensual como % del ingreso mensual
    declarado del cliente, para que quien decide una aprobación vea de un
    vistazo qué tan cerca (o cuánto por encima) está esta solicitud del tope
    del 40% de BR-LOAN-002, sin tener que calcularlo a mano con una
    calculadora aparte. Puramente informativo -- la validación real de
    BR-LOAN-002 ya la hace CreateLoan/UpdateLoanProposal en el servidor;
    esto no vuelve a aplicarla, solo la muestra."""
    if not client.declared_monthly_income:
        return "Sin ingreso declarado registrado", False
    try:
        ingreso = Decimal(client.declared_monthly_income)
        cuota = Decimal(loan.installment_amount or loan.principal_amount)
    except InvalidOperation:
        return "No se pudo calcular", False
    if ingreso <= 0:
        return "Ingreso declarado en cero", False
    ratio = (cuota / ingreso) * 100
    excede = ratio > 40
    texto = f"{ratio:.1f}% del ingreso declarado"
    if excede:
        texto += " — supera el 40% admitido por BR-LOAN-002"
    return texto, excede


def ficha_cliente_html(loan, client) -> str:
    """Ficha de cliente para el análisis de una solicitud de crédito: reúne
    en un solo papel todos los datos del cliente, sus tres referencias
    (BR-CLI-005), el origen de fondos (BR-CLI-006) y los términos del
    préstamo que está solicitando -- pensada para imprimirse y analizarse
    antes de decidir la aprobación, en vez de tener que abrir la ficha del
    cliente y la del préstamo por separado. Tiene más sentido con un
    préstamo PENDING, pero no está restringida a ese estado: nada impide
    reimprimirla para revisar un expediente ya aprobado.

    Sin banner de borrador: no tiene texto legal que revisar, solo datos ya
    registrados -- mismo criterio que el Cronograma y los reportes del
    dashboard. Tampoco es un documento que se entregue al cliente (a
    diferencia de los otros cinco): es de uso interno para la decisión de
    crédito.

    loan: loan_service_pb2.GetLoanByIdResponse
    client: client_service_pb2.GetClientByIdResponse"""
    estado_cliente = "Activo" if client.is_active else "Inactivo"
    estado_prestamo = _ESTADOS_LABEL.get(loan.status, loan.status)
    ratio_texto, ratio_excede = _relacion_cuota_ingreso(loan, client)
    ratio_color = theme.ERROR if ratio_excede else theme.TEXT_PRIMARY
    garantia = (
        f"{loan.guarantee_type} &mdash; Monto: {gs(loan.guarantee_amount)}"
        if loan.guarantee_type
        else "Sin garant&iacute;a registrada"
    )
    return f"""
    <html><body style="font-family: sans-serif; color: {theme.TEXT_PRIMARY};">
    {_header("Ficha de Cliente &mdash; An&aacute;lisis de Cr&eacute;dito")}
    <h3 style="color:{theme.PRIMARY};">Datos personales</h3>
    <p><b>Nombre completo:</b> {client.first_name} {client.last_name}<br/>
    <b>Documento (C.I.):</b> {client.national_id}<br/>
    <b>Fecha de nacimiento:</b> {fecha(client.date_of_birth)}<br/>
    <b>Estado del cliente:</b> {estado_cliente}<br/>
    <b>Cliente desde:</b> {fecha_hora(client.created_at.ToDatetime())}<br/>
    <b>Email:</b> {client.email}<br/>
    <b>Tel&eacute;fono:</b> {client.phone_number}<br/>
    <b>Direcci&oacute;n:</b> {client.address}</p>

    <h3 style="color:{theme.PRIMARY};">Situaci&oacute;n financiera declarada</h3>
    <p><b>Ingreso mensual declarado:</b>
    {gs(client.declared_monthly_income) or "No registrado"}<br/>
    <b>Origen de fondos:</b> {client.source_of_funds or "No registrado"}</p>

    <h3 style="color:{theme.PRIMARY};">Referencias</h3>
    <table border="1" cellspacing="0" cellpadding="6" width="100%">
      <tr style="background-color:{theme.APP_BACKGROUND};">
        <th align="left">Tipo</th><th align="left">Nombre</th>
        <th align="left">Relaci&oacute;n / Cargo</th><th align="left">Tel&eacute;fono</th>
      </tr>
      <tr><td>Referencia personal 1</td><td>{client.personal_reference_1_name}</td>
          <td>{client.personal_reference_1_relationship}</td>
          <td>{client.personal_reference_1_phone}</td></tr>
      <tr><td>Referencia personal 2</td><td>{client.personal_reference_2_name}</td>
          <td>{client.personal_reference_2_relationship}</td>
          <td>{client.personal_reference_2_phone}</td></tr>
      <tr><td>Referencia laboral</td><td>{client.employment_reference_employer}</td>
          <td>{client.employment_reference_position}
          ({client.employment_reference_seniority})</td>
          <td>{client.employment_reference_phone}</td></tr>
    </table>

    <h3 style="color:{theme.PRIMARY};">Pr&eacute;stamo solicitado</h3>
    <p><b>N&uacute;mero:</b> {loan.id}<br/>
    <b>Estado:</b> {estado_prestamo}<br/>
    <b>Capital solicitado:</b> {gs(loan.principal_amount)}<br/>
    <b>Plazo:</b> {loan.term_months} meses<br/>
    <b>Tasa de inter&eacute;s:</b> {rate_percent(loan.interest_rate)} anual
    ({rate_percent_mensual(loan.interest_rate)} mensual)<br/>
    <b>Cuota mensual:</b> {gs(loan.installment_amount)}<br/>
    <b>Total del cr&eacute;dito (capital + cargos):</b>
    {gs(loan.total_credit_with_charges)}<br/>
    <b>Total a pagar:</b> {gs(loan.total_to_pay)}<br/>
    <b>Garant&iacute;a:</b> {garantia}</p>
    <p style="color:{ratio_color}; font-weight:700;">Relaci&oacute;n cuota/ingreso:
    {ratio_texto}</p>

    {_footer("Ficha generada por el sistema de CREDIMED UME. Uso interno para el "
             "an&aacute;lisis y la decisi&oacute;n de aprobaci&oacute;n del cr&eacute;dito "
             "-- no constituye un documento legal ni se entrega al cliente.")}
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


# BR-DASH-003. El servidor devuelve el estado como enum de cable
# ("AL_DIA"/"CUOTA_VENCIDA"/"INCUMPLIDO"); la etiqueta que ve el usuario vive
# acá, igual que _ESTADOS_LABEL para LoanStatusEnum.
_ESTADO_PAGO_LABEL = {
    "AL_DIA": "Al día",
    "CUOTA_VENCIDA": "Cuota vencida",
    "INCUMPLIDO": "Incumplido",
}

# Columnas del reporte de estado de pago, compartidas por el PDF, el DOCX y la
# tabla de dashboard_view.py -- una sola definición para que los tres no se
# desincronicen, mismo criterio que _filas_reporte().
ESTADO_PAGOS_COLUMNAS = (
    "Cliente",
    "Documento",
    "Teléfono",
    "Préstamos",
    "Próximo vencimiento",
    "Saldo pendiente (Gs)",
    "Monto vencido (Gs)",
    "Estado",
)

# Índices de ESTADO_PAGOS_COLUMNAS que llevan un importe y por lo tanto se
# alinean a la derecha (en la tabla de la vista y en el PDF).
ESTADO_PAGOS_COLUMNAS_NUMERICAS = (5, 6)


def estado_pago_label(estado: str) -> str:
    return _ESTADO_PAGO_LABEL.get(estado, estado)


def _filas_estado_pagos(report) -> list[tuple[str, ...]]:
    """Una tupla por cliente, en el mismo orden que ESTADO_PAGOS_COLUMNAS.

    report: dashboard_service_pb2.GetClientPaymentStatusReportResponse"""
    filas = []
    for fila in report.rows:
        prestamos = str(fila.active_loans_count)
        if fila.defaulted_loans_count:
            prestamos += f" + {fila.defaulted_loans_count} incumplido(s)"
        proximo = "—"
        if fila.next_due_date:
            proximo = f"{fecha(fila.next_due_date)} · {gs(fila.next_due_amount)}"
        vencido = gs(fila.overdue_amount)
        if fila.overdue_installments_count:
            vencido += f" ({fila.overdue_installments_count} cuota/s)"
        filas.append(
            (
                fila.client_name,
                fila.national_id,
                fila.phone_number,
                prestamos,
                proximo,
                gs(fila.outstanding_balance),
                vencido,
                estado_pago_label(fila.payment_status),
            )
        )
    return filas


def reporte_estado_pagos_html(report, generated_by: str = "") -> str:
    """BR-DASH-003: estado de pago de los clientes, para imprimir y trabajar la
    cobranza.

    Sin banner de borrador, igual que el Cronograma y el Reporte de cierre de
    período: no tiene texto legal, solo cifras calculadas sobre datos ya
    registrados.

    report: dashboard_service_pb2.GetClientPaymentStatusReportResponse"""
    encabezados = "".join(
        f'<th align="{"right" if i in ESTADO_PAGOS_COLUMNAS_NUMERICAS else "left"}">'
        f"{columna}</th>"
        for i, columna in enumerate(ESTADO_PAGOS_COLUMNAS)
    )
    filas = "".join(
        "<tr>"
        + "".join(
            f'<td align="{"right" if i in ESTADO_PAGOS_COLUMNAS_NUMERICAS else "left"}">'
            f"{celda}</td>"
            for i, celda in enumerate(tupla)
        )
        + "</tr>"
        for tupla in _filas_estado_pagos(report)
    )
    if not filas:
        filas = (
            f'<tr><td colspan="{len(ESTADO_PAGOS_COLUMNAS)}">'
            "Sin clientes que informar con el filtro aplicado.</td></tr>"
        )

    alcance = (
        "Solo clientes con cuotas vencidas o pr&eacute;stamos incumplidos"
        if report.only_overdue
        else "Todos los clientes con cartera viva (pr&eacute;stamos activos o "
        "incumplidos)"
    )
    generado_por = f"<br/><b>Generado por:</b> {generated_by}" if generated_by else ""
    return f"""
    <html><body style="font-family: sans-serif; color: {theme.TEXT_PRIMARY};">
    {_header("Estado de Pago de Clientes")}
    <p><b>Fecha del reporte:</b> {fecha_hora(report.generated_at.ToDatetime())}<br/>
    <b>Alcance:</b> {alcance}{generado_por}</p>
    <p><b>Clientes informados:</b> {report.clients_count} &nbsp;&nbsp;
    <b>Con atraso:</b> {report.overdue_clients_count} &nbsp;&nbsp;
    <b>Saldo pendiente:</b> {gs(report.total_outstanding)} &nbsp;&nbsp;
    <b>Monto vencido:</b> {gs(report.total_overdue)}</p>
    <table border="1" cellspacing="0" cellpadding="5" width="100%">
      <tr style="background-color:{theme.PRIMARY}; color:white;">{encabezados}</tr>
      {filas}
    </table>
    <p style="font-size:12px; color:{theme.TEXT_MUTED}; margin-top:16px;">
    El "monto vencido" es lo ya exigible e impago; el "saldo pendiente"
    incluye adem&aacute;s las cuotas futuras todav&iacute;a no vencidas. Los
    totales corresponden a los clientes listados, no a toda la cartera.</p>
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
    un pr&eacute;stamo de dinero por un capital de
    <b>Guaran&iacute;es {gs(loan.principal_amount)}</b>, que se desembolsa a
    la firma del presente instrumento. {_clausula_objeto_texto(loan)}</p>
    <p><b>Segunda (Reembolso):</b> El(Los) Prestatario(s) se compromete(n) a
    reembolsar el pr&eacute;stamo otorgado en <b>{loan.term_months}</b>
    cuotas <b>iguales</b>, mensuales y consecutivas: cada cuota amortiza una
    porci&oacute;n constante del monto total del cr&eacute;dito e incluye un
    inter&eacute;s fijo calculado sobre ese mismo monto original, de modo que
    todas las cuotas son del mismo importe. La primera de ellas vence el d&iacute;a
    <b>{fecha(loan.first_due_date)}</b>, mediante transferencia
    bancaria, d&eacute;bito directo o descuento en cuenta, seg&uacute;n lo
    acordado.</p>
    <p><b>Tercera (Intereses):</b> Se acuerda el pago de un inter&eacute;s
    compensatorio del <b>{rate_percent_mensual(loan.interest_rate)}
    mensual</b>, calculado sobre el monto original del pr&eacute;stamo y
    abonado junto con las cuotas de amortizaci&oacute;n del capital. Para el
    caso de falta de pago en la fecha convenida, se aplicar&aacute; adem&aacute;s, sobre cada cuota
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
