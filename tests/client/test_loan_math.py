"""BR-LOAN-006/013: la inversa cuota->cargo de cas_client/loan_math.py.

El punto de estas pruebas es que la fórmula del cliente y el cronograma que
realmente calcula el servidor no se separen. Por eso comparan contra
`amortization.calcular_cronograma` en vez de contra montos escritos a mano:
un número a mano solo prueba que la fórmula no cambió, no que siga siendo la
misma que la del servidor.
"""

from decimal import Decimal

import pytest

from cas_client.loan_math import (
    CuotaInalcanzable,
    cargo_para_cuota_objetivo,
    cuota_estimada,
    tope_cargos,
)
from cas_server.services.amortization import calcular_cronograma

TASA = Decimal("0.20")  # BR-LOAN-007, tope legal de interés


def _cuota_real(capital, tasa, plazo, cargos):
    """La cuota tal como la produce el servidor: sobre capital + cargos
    (BR-LOAN-006 los capitaliza)."""
    return calcular_cronograma(capital + cargos, tasa, plazo)[0].monto_cuota


def test_el_caso_de_referencia_de_la_entidad():
    """2.348.590 Gs a 12 meses dan una cuota de 234.859; el operador la quiere
    en 250.000 y el excedente entra como gasto administrativo."""
    capital, plazo = Decimal("2348590"), 12
    assert _cuota_real(capital, TASA, plazo, Decimal("0")) == Decimal("234859.00")

    cargo = cargo_para_cuota_objetivo(capital, TASA, plazo, Decimal("250000"))
    assert cargo == Decimal("151410")
    assert _cuota_real(capital, TASA, plazo, cargo) == Decimal("250000.00")


@pytest.mark.parametrize(
    "capital,plazo,objetivo",
    [
        ("2348590", 12, "250000"),
        ("7000000", 12, "750000"),
        ("1000000", 6, "200000"),
        ("5000000", 24, "300000"),
        ("3500000", 18, "260000"),
    ],
)
def test_la_inversa_reproduce_la_cuota_pedida_en_el_cronograma_real(
    capital, plazo, objetivo
):
    """Para cada caso, el cargo que devuelve la inversa tiene que hacer que el
    cronograma del servidor dé exactamente la cuota pedida."""
    capital, objetivo = Decimal(capital), Decimal(objetivo)
    cargo = cargo_para_cuota_objetivo(capital, TASA, plazo, objetivo)
    real = _cuota_real(capital, TASA, plazo, cargo)
    # El cargo es un monto en guaraníes enteros (es lo que admite el campo del
    # formulario), así que la cuota puede caer unos céntimos por debajo del
    # objetivo; nunca por encima. En guaraníes eso es el mismo número.
    assert objetivo - real < Decimal("1"), (objetivo, real)
    assert real <= objetivo


def test_respeta_los_otros_cargos_ya_cargados():
    """El tope es sobre la suma de los cuatro cargos, así que el administrativo
    solo puede ocupar lo que los otros dejan libre."""
    capital, plazo = Decimal("2348590"), 12
    otros = Decimal("50000")
    cargo = cargo_para_cuota_objetivo(
        capital, TASA, plazo, Decimal("250000"), otros_cargos=otros
    )
    assert cargo == Decimal("101410")  # 151.410 - 50.000
    assert _cuota_real(capital, TASA, plazo, cargo + otros) == Decimal("250000.00")


def test_rechaza_una_cuota_por_encima_del_tope_del_25_por_ciento():
    """No recorta en silencio: un cargo por encima del tope lo rechazaría el
    servidor (BR-LOAN-006), así que es mejor decirlo acá y nombrar el máximo."""
    capital, plazo = Decimal("2348590"), 12
    with pytest.raises(CuotaInalcanzable) as exc:
        cargo_para_cuota_objetivo(capital, TASA, plazo, Decimal("400000"))
    assert "más alta posible" in str(exc.value)


def test_la_cuota_maxima_que_anuncia_el_error_es_realmente_alcanzable():
    """El mensaje nombra un máximo: ese número tiene que ser cierto contra el
    cronograma real, si no manda al operador a probar algo que tampoco entra."""
    capital, plazo = Decimal("2348590"), 12
    techo = tope_cargos(capital, Decimal("0.25"), plazo)
    maxima = cuota_estimada(capital, TASA, plazo, techo)
    assert _cuota_real(capital, TASA, plazo, techo) == maxima
    # y pedir exactamente esa cuota tiene que entrar sin pasarse del tope
    # (redondear el cargo hacia arriba lo habría cruzado por 0,50 Gs y el
    # servidor lo habría rechazado con INVALID_ARGUMENT)
    cargo = cargo_para_cuota_objetivo(capital, TASA, plazo, maxima)
    assert cargo <= techo


def test_rechaza_una_cuota_menor_a_la_que_da_el_capital_solo():
    """Los cargos suman al capital, nunca lo reducen: no hay cargo negativo que
    baje la cuota por debajo de la del préstamo sin cargos."""
    capital, plazo = Decimal("2348590"), 12
    with pytest.raises(CuotaInalcanzable) as exc:
        cargo_para_cuota_objetivo(capital, TASA, plazo, Decimal("200000"))
    assert "no puede bajar" in str(exc.value)


def test_pedir_exactamente_la_cuota_sin_cargos_da_cargo_cero():
    capital, plazo = Decimal("2348590"), 12
    assert cargo_para_cuota_objetivo(
        capital, TASA, plazo, Decimal("234859")
    ) == Decimal("0")


def test_plazo_o_capital_invalidos_no_revientan():
    with pytest.raises(CuotaInalcanzable):
        cargo_para_cuota_objetivo(Decimal("1000"), TASA, 0, Decimal("100"))
    with pytest.raises(CuotaInalcanzable):
        cargo_para_cuota_objetivo(Decimal("0"), TASA, 12, Decimal("100"))
