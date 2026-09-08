"""Aritmética de propuesta que el cliente necesita *antes* de que el préstamo
exista, y por lo tanto antes de que el servidor pueda calcular nada.

Funciones puras, sin Qt ni gRPC. Todo lo que se calcule acá es una **comodidad
de carga**: el servidor recalcula el cronograma (`amortization.calcular_cronograma`)
y vuelve a hacer valer el tope de cargos (BR-LOAN-006) al guardar. Si alguna vez
discrepan, manda el servidor -- por eso `tests/client/test_loan_math.py` compara
esta inversa contra el cronograma real en vez de contra números escritos a mano.
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from cas_client.formatting import gs


# Excepción de dominio en vez de devolver None: quien llama necesita explicarle
# al operador *por qué* no se puede, y cada motivo tiene un mensaje distinto.
class CuotaInalcanzable(Exception):
    """La cuota objetivo no se puede lograr con los cargos permitidos."""


def factor_cuota(tasa_anual: Decimal, plazo_meses: int) -> Decimal:
    """Cuánto de cuota genera cada guaraní financiado, bajo BR-LOAN-013.

    En el sistema alemán de esta entidad la cuota es `financiado/plazo +
    financiado*tasa/12`: las dos componentes son constantes y el interés se
    calcula sobre el monto original, nunca sobre el saldo. Así que la cuota es
    proporcional al monto financiado, y esa proporcionalidad es lo que permite
    invertir la fórmula acá abajo.
    """
    return Decimal(1) / Decimal(plazo_meses) + tasa_anual / Decimal(12)


def cuota_estimada(
    capital: Decimal, tasa_anual: Decimal, plazo_meses: int, total_cargos: Decimal
) -> Decimal:
    """Cuota que resultará de este capital más estos cargos (BR-LOAN-006: los
    cargos se capitalizan, así que amortizan e intereses corren sobre ellos)."""
    financiado = capital + total_cargos
    return (financiado * factor_cuota(tasa_anual, plazo_meses)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def tope_cargos(capital: Decimal, ratio_maximo: Decimal, plazo_meses: int) -> Decimal:
    """BR-LOAN-006: techo de la suma de cargos, prorrateado por el plazo con la
    misma mecánica que BR-LOAN-013 usa para el interés."""
    return (capital * ratio_maximo / Decimal(12) * plazo_meses).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def cargo_para_cuota_objetivo(
    capital: Decimal,
    tasa_anual: Decimal,
    plazo_meses: int,
    cuota_objetivo: Decimal,
    otros_cargos: Decimal = Decimal("0"),
    ratio_maximo: Decimal = Decimal("0.25"),
) -> Decimal:
    """Cargo administrativo que hace que la cuota dé `cuota_objetivo`.

    Es la inversa de `cuota_estimada`. Existe porque el operador razona en
    cuotas redondas ("que pague 250.000"), no en cargos: sin esto tendría que
    tantear el monto del cargo hasta que la cuota diera lo que quiere.

    **El excedente entra como cargo, nunca como interés.** La tasa está fijada
    por ley en el 20% (BR-LOAN-007) y no se toca; lo que sube la cuota es un
    gasto administrativo financiado, acotado por el tope del 25%
    (BR-LOAN-006) -- que es exactamente el margen que la entidad ya usa. Por
    eso el resultado se valida contra `tope_cargos` y no se recorta en
    silencio: devolver un cargo que el servidor va a rechazar sería peor que
    decir que la cuota pedida no se puede.

    `otros_cargos` son los otros tres cargos ya cargados en el formulario: el
    tope es sobre la *suma*, así que el administrativo solo puede ocupar lo
    que quede libre.
    """
    if plazo_meses <= 0:
        raise CuotaInalcanzable("El plazo debe ser mayor a cero.")
    if capital <= 0:
        raise CuotaInalcanzable("El capital debe ser mayor a cero.")

    financiado_objetivo = cuota_objetivo / factor_cuota(tasa_anual, plazo_meses)
    cargos_totales = financiado_objetivo - capital

    minimo = cuota_estimada(capital, tasa_anual, plazo_meses, otros_cargos)
    if cargos_totales < otros_cargos:
        raise CuotaInalcanzable(
            f"La cuota no puede bajar de {gs(str(minimo))}: los cargos se "
            f"suman al capital, nunca lo reducen."
        )

    techo = tope_cargos(capital, ratio_maximo, plazo_meses)
    if cargos_totales > techo:
        maxima = cuota_estimada(capital, tasa_anual, plazo_meses, techo)
        raise CuotaInalcanzable(
            f"La cuota más alta posible es {gs(str(maxima))}: exigiría cargos "
            f"por encima del tope de {gs(str(techo))}."
        )

    # Hacia abajo, nunca al más cercano, por dos razones que apuntan al mismo
    # lado: redondear hacia arriba puede empujar la suma de cargos por encima
    # del tope (el servidor la rechazaría) y haría pagar al deudor un poco más
    # de lo que el operador pidió. Con ROUND_DOWN la cuota resultante queda en
    # el objetivo o unos céntimos por debajo -- invisible en guaraníes, que no
    # usan centavos.
    return (cargos_totales - otros_cargos).quantize(Decimal("1"), rounding=ROUND_DOWN)


@dataclass(frozen=True)
class CuotaEstimada:
    numero: int
    capital: Decimal
    interes: Decimal
    cuota: Decimal
    saldo: Decimal


def cronograma_estimado(
    capital: Decimal,
    tasa_anual: Decimal,
    plazo_meses: int,
    total_cargos: Decimal = Decimal("0"),
) -> list[CuotaEstimada]:
    """Vista previa del cronograma completo de una propuesta que todavía no
    existe como préstamo -- por eso no puede pedirse con GetAmortizationSchedule,
    que necesita un `loan_id` ya guardado. Existe para que el operador vea el
    efecto de tocar capital/plazo/cargos sin tener que guardar la propuesta,
    volver atrás y volver a entrar para revisar la cuota resultante.

    Reproduce BR-LOAN-013 (capital e interés constantes cuota a cuota, interés
    calculado una sola vez sobre el monto financiado original -- nunca sobre
    el saldo -- y la última cuota absorbiendo el redondeo) tal como
    `cas_server/services/amortization.calcular_cronograma` la calcula sobre un
    préstamo real, sin `ajustes` (BR-LOAN-008 sólo existe una vez que el
    préstamo está ACTIVE) ni fechas de vencimiento (el primer vencimiento
    puede no estar cargado todavía en el formulario). `total_cargos` es la
    suma de los cuatro cargos capitalizados (BR-LOAN-006): igual que en el
    servidor, se financian junto con el capital.
    """
    if plazo_meses <= 0:
        raise CuotaInalcanzable("El plazo debe ser mayor a cero.")
    if capital <= 0:
        raise CuotaInalcanzable("El capital debe ser mayor a cero.")

    financiado = capital + total_cargos
    interes = (financiado * tasa_anual / Decimal(12)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    amortizacion = (financiado / Decimal(plazo_meses)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    filas: list[CuotaEstimada] = []
    saldo = financiado
    for numero in range(1, plazo_meses + 1):
        capital_cuota = saldo if numero == plazo_meses else amortizacion
        saldo -= capital_cuota
        filas.append(
            CuotaEstimada(
                numero=numero,
                capital=capital_cuota,
                interes=interes,
                cuota=capital_cuota + interes,
                saldo=saldo,
            )
        )
    return filas
