"""Cálculo del cronograma de amortización de préstamos (sistema francés).

Funciones puras, sin I/O -- el cronograma se calcula bajo demanda a partir de
capital/tasa/plazo en lugar de persistirse (el modelo de datos de
specs/loans/README no incluye una tabla de cronograma/cuotas).
"""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

CENTAVOS = Decimal("0.01")


@dataclass(frozen=True)
class Cuota:
    numero: int
    monto_cuota: Decimal
    capital: Decimal
    interes: Decimal
    saldo: Decimal
    fecha_vencimiento: date | None = None
    ajustada: bool = False


def _centavos(valor: Decimal) -> Decimal:
    return valor.quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def _sumar_meses(fecha: date, meses: int) -> date:
    indice_mes = fecha.month - 1 + meses
    anio = fecha.year + indice_mes // 12
    mes = indice_mes % 12 + 1
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    return date(anio, mes, min(fecha.day, ultimo_dia))


def calcular_cronograma(
    capital: Decimal,
    tasa_anual: Decimal,
    plazo_meses: int,
    fecha_primer_vencimiento: date | None = None,
    ajustes: dict[int, Decimal] | None = None,
) -> list[Cuota]:
    """Sistema francés: cronograma de amortización con cuota fija (anualidad).

    `tasa_anual` es una tasa nominal anual (p. ej. Decimal("0.24") = 24%/año,
    capitalizable mensualmente); la tasa aplicada por período es
    tasa_anual / 12. Todos los montos se redondean a centavos. La porción de
    capital de la última cuota se ajusta al saldo restante exacto para que la
    suma acumulada de capital sea exactamente igual a `capital`, pese al
    redondeo de cada período.

    `ajustes` (opcional) mapea número de cuota -> monto de cuota manualmente
    fijado (ver BR-LOAN-008/UpdateInstallmentAmount): reemplaza la cuota fija
    calculada para ese número únicamente. El principal del préstamo
    (`capital`) nunca cambia por un ajuste -- lo único que se recalcula es
    cómo se reparte entre las cuotas restantes. Inmediatamente después de una
    cuota ajustada, las cuotas siguientes (aún no ajustadas ni la última) se
    re-amortizan con una nueva cuota fija calculada sobre el saldo resultante
    y la cantidad de períodos que quedan -- misma fórmula de anualidad que la
    cuota original, solo que arrancando desde el nuevo saldo -- para que el
    cambio se absorba de forma pareja entre las cuotas que quedan en vez de
    acumularse entero en la última. La última cuota nunca es directamente
    ajustable: sigue forzada a saldar el remanente exacto (así el capital
    total pagado siempre coincide con `capital`, centavo a centavo, pese al
    redondeo de cada período). Esta función es pura y no valida los ajustes
    (p. ej. que dejen saldo negativo) -- eso es responsabilidad de quien
    llama (loan_service.py).
    """
    ajustes = ajustes or {}
    tasa_mensual = tasa_anual / Decimal(12)

    def _cuota_fija(saldo_base: Decimal, periodos: int) -> Decimal:
        if tasa_mensual == 0:
            return _centavos(saldo_base / periodos)
        factor = (1 + tasa_mensual) ** periodos
        return _centavos(saldo_base * tasa_mensual * factor / (factor - 1))

    cuota = _cuota_fija(capital, plazo_meses)

    filas: list[Cuota] = []
    saldo = capital
    for numero in range(1, plazo_meses + 1):
        interes = _centavos(saldo * tasa_mensual)
        ajustada = numero != plazo_meses and numero in ajustes
        if numero == plazo_meses:
            capital_cuota = saldo
            monto_cuota = capital_cuota + interes
        elif ajustada:
            monto_cuota = ajustes[numero]
            capital_cuota = monto_cuota - interes
        else:
            capital_cuota = cuota - interes
            monto_cuota = cuota
        saldo = saldo - capital_cuota
        if ajustada:
            # Re-amortiza el saldo restante sobre los períodos que faltan
            # (incluyendo la última, igual que el cálculo inicial de `cuota`
            # sobre plazo_meses) para que las próximas cuotas no ajustadas
            # absorban el cambio de a poco en vez de todo de golpe en la
            # última.
            cuota = _cuota_fija(saldo, plazo_meses - numero)
        fecha_vencimiento = (
            _sumar_meses(fecha_primer_vencimiento, numero - 1)
            if fecha_primer_vencimiento is not None
            else None
        )
        filas.append(
            Cuota(
                numero=numero,
                monto_cuota=monto_cuota,
                capital=capital_cuota,
                interes=interes,
                saldo=saldo,
                fecha_vencimiento=fecha_vencimiento,
                ajustada=ajustada,
            )
        )
    return filas
