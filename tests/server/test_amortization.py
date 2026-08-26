from datetime import date
from decimal import Decimal

from cas_server.services.amortization import _sumar_meses, calcular_cronograma


def test_constant_principal_invariant_and_exact_principal_sum():
    """BR-LOAN-013 (sistema alemán): lo constante es la porción de CAPITAL de
    cada cuota, no el importe de la cuota -- que es justo al revés del sistema
    francés que este módulo implementaba antes."""
    filas = calcular_cronograma(Decimal("15000.00"), Decimal("0.24"), 12)

    assert len(filas) == 12
    capitales_no_finales = {fila.capital for fila in filas[:-1]}
    assert capitales_no_finales == {Decimal("1250.00")}  # 15000 / 12

    assert sum(fila.capital for fila in filas) == Decimal("15000.00")
    assert filas[-1].saldo == Decimal("0.00")


def test_interest_is_flat_on_the_original_principal_not_on_the_balance():
    """BR-LOAN-013: el interés NO varía. Se calcula una sola vez sobre el
    capital original, así que no baja aunque el saldo baje -- que es
    exactamente lo que lo distingue del sistema alemán de manual (y del
    francés, donde el interés también decrece)."""
    filas = calcular_cronograma(Decimal("15000.00"), Decimal("0.24"), 12)

    assert {fila.interes for fila in filas} == {Decimal("300.00")}  # 15000 * 2%
    # Interés total = capital * tasa mensual * plazo, no menos.
    assert sum(fila.interes for fila in filas) == Decimal("3600.00")


def test_every_installment_is_equal_except_the_rounding_on_the_last():
    """Capital constante + interés constante => cuota constante. La última
    difiere a lo sumo en centavos porque absorbe el redondeo del capital."""
    filas = calcular_cronograma(Decimal("15000.00"), Decimal("0.24"), 12)

    assert {fila.monto_cuota for fila in filas[:-1]} == {Decimal("1550.00")}
    assert abs(filas[-1].monto_cuota - filas[0].monto_cuota) < Decimal("0.10")


def test_reference_case_with_the_standard_rate():
    """Caso de referencia con la tasa fija vigente (BR-LOAN-007, 45% anual =
    3,75% mensual), para que un cambio silencioso de la fórmula o de la tasa
    no pase inadvertido. Es el mismo préstamo de los documentos de muestra."""
    filas = calcular_cronograma(Decimal("7000000.00"), Decimal("0.45"), 12)

    assert filas[0].capital == Decimal("583333.33")  # 7.000.000 / 12
    assert filas[0].interes == Decimal("262500.00")  # 7.000.000 * 3,75%
    assert filas[0].monto_cuota == Decimal("845833.33")
    assert sum(fila.monto_cuota for fila in filas) == Decimal("10150000.00")
    assert sum(fila.interes for fila in filas) == Decimal("3150000.00")


def test_zero_interest_splits_principal_evenly():
    filas = calcular_cronograma(Decimal("1200.00"), Decimal("0"), 12)

    assert all(fila.interes == Decimal("0.00") for fila in filas)
    assert all(fila.monto_cuota == Decimal("100.00") for fila in filas)
    assert filas[-1].saldo == Decimal("0.00")


def test_single_installment_pays_principal_plus_interest():
    filas = calcular_cronograma(Decimal("1000.00"), Decimal("0.12"), 1)

    assert len(filas) == 1
    assert filas[0].capital == Decimal("1000.00")
    assert filas[0].interes == Decimal("10.00")
    assert filas[0].monto_cuota == Decimal("1010.00")
    assert filas[0].saldo == Decimal("0.00")


def test_sumar_meses_handles_month_end_clamping():
    assert _sumar_meses(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_sumar_meses_handles_leap_year():
    assert _sumar_meses(date(2028, 1, 31), 1) == date(2028, 2, 29)


def test_sumar_meses_handles_year_rollover():
    assert _sumar_meses(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_calcular_cronograma_without_anchor_date_has_no_due_dates():
    filas = calcular_cronograma(Decimal("1000.00"), Decimal("0.12"), 6)
    assert all(fila.fecha_vencimiento is None for fila in filas)


def test_calcular_cronograma_with_anchor_date_sets_monthly_due_dates():
    primer_vencimiento = date(2026, 9, 1)
    filas = calcular_cronograma(
        Decimal("1000.00"),
        Decimal("0.12"),
        3,
        fecha_primer_vencimiento=primer_vencimiento,
    )
    assert [fila.fecha_vencimiento for fila in filas] == [
        date(2026, 9, 1),
        date(2026, 10, 1),
        date(2026, 11, 1),
    ]


def test_ajustes_overrides_installment_amount_and_cascades_balance():
    sin_ajustes = calcular_cronograma(Decimal("15000.00"), Decimal("0.24"), 12)
    con_ajustes = calcular_cronograma(
        Decimal("15000.00"),
        Decimal("0.24"),
        12,
        ajustes={3: Decimal("2000.00")},
    )

    fila_ajustada = con_ajustes[2]
    assert fila_ajustada.numero == 3
    assert fila_ajustada.ajustada is True
    assert fila_ajustada.monto_cuota == Decimal("2000.00")
    assert fila_ajustada.capital == Decimal("2000.00") - fila_ajustada.interes

    # Tras el ajuste, las cuotas que quedan vuelven a amortizar una porción
    # constante -- del saldo NUEVO, no del original (BR-LOAN-013).
    capitales_posteriores = {fila.capital for fila in con_ajustes[3:-1]}
    assert len(capitales_posteriores) == 1
    assert capitales_posteriores != {sin_ajustes[3].capital}

    # El interés de la cuota ajustada no cambia -- depende del capital
    # original, que un ajuste nunca toca (BR-LOAN-013) -- pero el saldo
    # posterior a partir de ahí sí diverge del cronograma sin ajustar.
    assert fila_ajustada.interes == sin_ajustes[2].interes
    assert con_ajustes[3].saldo != sin_ajustes[3].saldo

    # La última cuota sigue saldando el remanente exacto pese al ajuste.
    assert con_ajustes[-1].saldo == Decimal("0.00")
    assert sum(fila.capital for fila in con_ajustes) == Decimal("15000.00")


def test_ajustes_ignores_override_on_last_installment():
    sin_ajustes = calcular_cronograma(Decimal("1000.00"), Decimal("0.12"), 3)
    con_ajustes = calcular_cronograma(
        Decimal("1000.00"),
        Decimal("0.12"),
        3,
        ajustes={3: Decimal("999999.00")},
    )

    assert con_ajustes[-1].ajustada is False
    assert con_ajustes[-1].monto_cuota == sin_ajustes[-1].monto_cuota
    assert con_ajustes[-1].saldo == Decimal("0.00")


def test_ajustes_none_behaves_like_no_ajustes():
    con_ajustes_vacio = calcular_cronograma(
        Decimal("1000.00"), Decimal("0.12"), 6, ajustes={}
    )
    sin_ajustes = calcular_cronograma(Decimal("1000.00"), Decimal("0.12"), 6)
    assert con_ajustes_vacio == sin_ajustes
