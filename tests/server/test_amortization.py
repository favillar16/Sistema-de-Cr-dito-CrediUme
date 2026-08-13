from datetime import date
from decimal import Decimal

from cas_server.services.amortization import _sumar_meses, calcular_cronograma


def test_fixed_payment_invariant_and_exact_principal_sum():
    filas = calcular_cronograma(Decimal("15000.00"), Decimal("0.24"), 12)

    assert len(filas) == 12
    cuotas_no_finales = {fila.monto_cuota for fila in filas[:-1]}
    assert len(cuotas_no_finales) == 1  # toda cuota no final es idéntica

    assert sum(fila.capital for fila in filas) == Decimal("15000.00")
    assert filas[-1].saldo == Decimal("0.00")


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

    # El interés de la cuota ajustada no cambia (depende del saldo previo,
    # que es el mismo hasta ese punto), pero el saldo posterior a partir de
    # ahí sí diverge del cronograma sin ajustar.
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
