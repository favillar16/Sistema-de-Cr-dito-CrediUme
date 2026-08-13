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
neither was copied: the company identity below is CrediUME's own
(placeholder pending real RUC), and every borrower-identifying field is
pulled from `client`/`loan`, never hardcoded.

The moratory/punitory interest rates, the number of consecutive unpaid
installments that triggers acceleration, and the jurisdiction city are still
literal placeholders (see the _PLACEHOLDER_* constants below) -- adapting the
structure from a real document is not the same as legal review, and these
figures are CrediUME's own business decisions to make, not something to
infer from another institution's contract. Every document still carries a
visible draft banner. Do not use in production without real legal review
(see CLAUDE.md)."""

from cas_client import assets, theme
from cas_client.formatting import gs, rate_percent

# Placeholder legal-entity data matching HeaderDocumentos.png's own placeholder
# RUC pattern ("80XXXXXXX-X") -- real values pending the same legal review the
# draft banner below already flags; swap in the real RUC once available.
_COMPANY_NAME = "CREDIUME S.A."
_COMPANY_RUC = "80XXXXXXX-X"
_COMPANY_ADDRESS = "Servicios Financieros — Coronel Oviedo, Py"

# Business terms CrediUME still needs to decide -- not inferable from the
# reference document (that was a different institution's own commercial
# terms). Kept as one place to swap in real values once decided.
_PLACEHOLDER_MORATORY_RATE = "[TASA MORATORIA A DEFINIR]% mensual"
_PLACEHOLDER_PUNITIVE_RATE = "[TASA PUNITORIA A DEFINIR]% mensual"
_PLACEHOLDER_ACCELERATION_INSTALLMENTS = "[N]"
_PLACEHOLDER_JURISDICTION_CITY = "[CIUDAD A DEFINIR]"

_ESTADOS_LABEL = {
    "PENDING": "Pendiente",
    "APPROVED": "Aprobado",
    "ACTIVE": "Activo",
    "PAID": "Pagado",
    "DEFAULTED": "Incumplido",
    "EXPIRED": "Caducado",
}

_DRAFT_BANNER = f"""
<div style="border: 2px solid {theme.ERROR}; color: {theme.ERROR};
            padding: 8px 12px; font-weight: 600; font-size: 12px; margin-bottom: 16px;">
BORRADOR &mdash; TEXTO LEGAL PENDIENTE DE REVISI&Oacute;N. No usar en producci&oacute;n
sin validaci&oacute;n legal.
</div>
"""


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
    {_DRAFT_BANNER}
    {_client_block(client)}
    <p><b>Pr&eacute;stamo:</b> {loan.id}<br/>
    <b>Estado:</b> {estado}<br/>
    <b>Capital:</b> {gs(loan.principal_amount)}<br/>
    <b>Tasa de interés:</b> {rate_percent(loan.interest_rate)}<br/>
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
    {_footer("Documento generado por el sistema CrediUME. V&aacute;lido &uacute;nicamente "
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
    {_DRAFT_BANNER}
    {_client_block(client)}
    <p>{_garantia_linea(loan)}</p>
    <p>DECLARO(AMOS) ADEUDAR a {_COMPANY_NAME} la suma de
    <b>Guaran&iacute;es {gs(loan.principal_amount)}</b>, que PAGAR&Eacute;(MOS)
    solidariamente, a su orden, libre de gastos y sin protesto, en
    <b>{loan.term_months}</b> cuotas iguales, mensuales y consecutivas, con
    vencimiento la primera de ellas el d&iacute;a <b>{loan.first_due_date}</b>,
    y las siguientes cuotas en esas mismas fechas de los meses subsiguientes
    hasta su total cancelaci&oacute;n, en el domicilio de {_COMPANY_NAME},
    sito en {_COMPANY_ADDRESS}.</p>
    <p>Queda expresamente pactado que los importes de las cuotas
    documentadas en este instrumento devengar&aacute;n un inter&eacute;s
    compensatorio del <b>{rate_percent(loan.interest_rate)}</b> sobre saldos
    deudores. En caso de mora, un inter&eacute;s moratorio del
    <b>{_PLACEHOLDER_MORATORY_RATE}</b> sobre saldos deudores, y un
    inter&eacute;s punitorio del <b>{_PLACEHOLDER_PUNITIVE_RATE}</b> sobre
    cada cuota en mora.</p>
    <p>La falta de pago de <b>{_PLACEHOLDER_ACCELERATION_INSTALLMENTS}</b>
    cuotas consecutivas de amortizaci&oacute;n del capital har&aacute;
    autom&aacute;ticamente exigible el total adeudado, inclusive las cuotas
    no vencidas, produci&eacute;ndose la mora por el mero vencimiento del
    plazo, sin necesidad de ning&uacute;n requerimiento judicial y/o
    extrajudicial.</p>
    <p>Todas las partes intervinientes en este documento se someten a la
    jurisdicci&oacute;n y competencia de los Jueces y Tribunales de
    <b>{_PLACEHOLDER_JURISDICTION_CITY}</b>.</p>
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
    (loan.created_by_username, "" si no se conoce -- pr&eacute;stamos previos
    a este campo) para que el cliente sepa a qui&eacute;n dirigirse.

    loan: loan_service_pb2.GetLoanByIdResponse
    client: client_service_pb2.GetClientByIdResponse
    schedule: loan_service_pb2.GetAmortizationScheduleResponse"""
    asesor = loan.created_by_username or "No registrado"
    filas = "".join(
        f"<tr><td>{i.installment_number}</td><td>{i.due_date}</td>"
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
    <b>Primer vencimiento:</b> {loan.first_due_date}</p>
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


def contrato_html(loan, client) -> str:
    return f"""
    <html><body style="font-family: sans-serif; color: {theme.TEXT_PRIMARY};">
    {_header("Contrato de Pr&eacute;stamo")}
    {_DRAFT_BANNER}
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
    ellas el d&iacute;a <b>{loan.first_due_date}</b>, mediante transferencia
    bancaria, d&eacute;bito directo o descuento en cuenta, seg&uacute;n lo
    acordado.</p>
    <p><b>Tercera (Intereses):</b> Se acuerda el pago de un inter&eacute;s
    compensatorio del <b>{rate_percent(loan.interest_rate)}</b> sobre saldos
    deudores, abonado junto con las cuotas de amortizaci&oacute;n del
    capital. Para el caso de falta de pago en la fecha convenida, se
    aplicar&aacute; adem&aacute;s un inter&eacute;s moratorio del
    <b>{_PLACEHOLDER_MORATORY_RATE}</b> y un inter&eacute;s punitorio del
    <b>{_PLACEHOLDER_PUNITIVE_RATE}</b> sobre los montos o cuotas vencidas e
    impagas.</p>
    <p><b>Cuarta (Mora y vencimiento anticipado):</b> La mora se
    producir&aacute; por el mero vencimiento de los plazos, sin necesidad de
    interpelaci&oacute;n judicial alguna. La falta de pago de
    <b>{_PLACEHOLDER_ACCELERATION_INSTALLMENTS}</b> cuotas consecutivas de
    amortizaci&oacute;n del capital y/o de sus intereses har&aacute; decaer
    de pleno derecho todos los plazos estipulados para el pago, facultando a
    {_COMPANY_NAME} a declarar vencidas todas las cuotas y exigir el pago de
    la totalidad de la deuda, ejecutando para el efecto el Pagar&eacute;
    suscripto junto con este contrato.</p>
    <p><b>Quinta (Central de riesgo crediticio):</b> El(Los) Prestatario(s)
    autoriza(n) expresamente a {_COMPANY_NAME} para que, por su cuenta o a
    trav&eacute;s de terceros, recabe informaci&oacute;n sobre su
    situaci&oacute;n patrimonial y crediticia, y para que, en caso de atraso
    en el pago, informe sus datos a las centrales de riesgo crediticio
    correspondientes, conforme a la legislaci&oacute;n vigente.</p>
    <p><b>Sexta (Jurisdicci&oacute;n):</b> Todas las partes intervinientes en
    este contrato se someten a la jurisdicci&oacute;n y competencia de los
    Jueces y Tribunales de <b>{_PLACEHOLDER_JURISDICTION_CITY}</b>.</p>
    <p><b>Pr&eacute;stamo:</b> {loan.id}</p>
    <p style="margin-top:48px;">Firma del deudor: ______________________________</p>
    {_footer("Firma de " + _COMPANY_NAME + ": ______________________________")}
    </body></html>
    """
