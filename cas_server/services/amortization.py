"""Cálculo del cronograma de amortización de préstamos (sistema alemán).

Funciones puras, sin I/O -- el cronograma se calcula bajo demanda a partir de
capital/tasa/plazo en lugar de persistirse (el modelo de datos de
specs/loans/README no incluye una tabla de cronograma/cuotas).
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
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


@dataclass(frozen=True)
class MoraCuota:
    """Mora devengada por **una** cuota vencida e impaga a una fecha dada."""

    numero: int
    dias_punibles: int
    monto: Decimal


def _centavos(valor: Decimal) -> Decimal:
    return valor.quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def calcular_mora_cuota(
    numero: int,
    pendiente: Decimal,
    fecha_vencimiento: date,
    hoy: date,
    *,
    tasa_mensual: Decimal,
    dias_gracia: int,
    dias_por_mes: int,
    desde: date | None = None,
) -> MoraCuota:
    """BR-LOAN-017: interés moratorio de una cuota vencida, a `hoy`.

    `pendiente * tasa_mensual / dias_por_mes * dias_punibles`, donde los días
    punibles se cuentan **desde el día de gracia en adelante**: la cláusula
    firmada dice que la mora "se devengará a partir de los 5 días corridos
    contados desde la fecha de su primer vencimiento", así que al sexto día de
    atraso se debe un día de mora, no seis.

    `desde` es un piso de calendario (config.LOAN_LATE_FEE_START_DATE): antes
    de esa fecha no se devenga nada, cualquiera sea el vencimiento de la
    cuota. Existe porque la mora se deriva de fechas y no se persiste -- sin
    el piso, el día que la regla entra en vigencia toda la cartera ya vencida
    aparecería debiendo meses de recargo que nunca se cobraron.

    Función pura, como el resto del módulo: no sabe qué préstamo es ni mira la
    base. El número de cuota viaja en el resultado sólo para que quien llama
    pueda imputar la mora a la cuota que la generó.
    """
    inicio = fecha_vencimiento + timedelta(days=dias_gracia)
    if desde is not None and desde > inicio:
        inicio = desde
    dias_punibles = max((hoy - inicio).days, 0)
    monto = (
        _centavos(pendiente * tasa_mensual / Decimal(dias_por_mes) * dias_punibles)
        if dias_punibles and pendiente > 0
        else Decimal("0.00")
    )
    return MoraCuota(numero=numero, dias_punibles=dias_punibles, monto=monto)


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
    """Sistema alemán (BR-LOAN-013): capital constante e interés fijo.

    Las dos componentes de la cuota son constantes:

    -   **Capital:** `capital / plazo_meses` en cada cuota.
    -   **Interés:** `capital * tasa_anual / 12` en cada cuota, calculado
        **siempre sobre el monto original del préstamo** y no sobre el saldo
        que va quedando.

    Por lo tanto la cuota total es la misma todos los meses (salvo la última,
    que absorbe los centavos del redondeo del capital), y el interés total del
    préstamo es `capital * tasa_mensual * plazo_meses`.

    **Esto es lo que la entidad llama "sistema alemán" y es una decisión
    comercial, no un descuido.** Difiere del sistema alemán de manual, que
    amortiza capital constante pero cobra el interés sobre el *saldo deudor*
    (cuota decreciente); acá el interés no varía. No "corregirlo" a saldos
    deudores: cambiaría lo que paga cada deudor y contradiría el Pagaré y el
    Contrato, que declaran el interés sobre el monto original (ver
    `cas_client/documents.py`). Tampoco es el sistema francés, que este módulo
    implementó hasta 2026-08-26: ahí lo constante era la cuota y lo que
    variaba era el reparto interno entre capital e interés.

    Todos los montos se redondean a centavos. La porción de capital de la
    última cuota se ajusta al saldo restante exacto para que la suma acumulada
    de capital sea exactamente igual a `capital`, pese al redondeo de cada
    período -- por eso esa última cuota puede diferir de las demás en algunos
    centavos.

    `ajustes` (opcional) mapea número de cuota -> monto de cuota manualmente
    fijado (ver BR-LOAN-008/UpdateInstallmentAmount): reemplaza la cuota
    calculada para ese número únicamente, y la porción de capital de esa cuota
    pasa a ser lo que sobra después de cubrir su interés. El interés **no** se
    recalcula por un ajuste: depende del capital original, que no cambia. El
    principal del préstamo tampoco cambia -- lo único que se recalcula es cómo
    se reparte entre las cuotas restantes: inmediatamente después de una cuota
    ajustada, las cuotas siguientes (aún no ajustadas ni la última) vuelven a
    amortizar una porción constante calculada sobre el saldo resultante y la
    cantidad de períodos que quedan, para que el cambio se absorba de forma
    pareja en vez de acumularse entero en la última. La última cuota nunca es
    directamente ajustable: sigue forzada a saldar el remanente exacto. Esta
    función es pura y no valida los ajustes (p. ej. que dejen saldo negativo)
    -- eso es responsabilidad de quien llama (loan_service.py).
    """
    ajustes = ajustes or {}
    tasa_mensual = tasa_anual / Decimal(12)

    # Interés del período, idéntico en todas las cuotas: se calcula una sola
    # vez sobre el capital original y no vuelve a mirar el saldo.
    interes = _centavos(capital * tasa_mensual)

    def _amortizacion_constante(saldo_base: Decimal, periodos: int) -> Decimal:
        return _centavos(saldo_base / periodos)

    amortizacion = _amortizacion_constante(capital, plazo_meses)

    filas: list[Cuota] = []
    saldo = capital
    for numero in range(1, plazo_meses + 1):
        ajustada = numero != plazo_meses and numero in ajustes
        if numero == plazo_meses:
            capital_cuota = saldo
            monto_cuota = capital_cuota + interes
        elif ajustada:
            monto_cuota = ajustes[numero]
            capital_cuota = monto_cuota - interes
        else:
            capital_cuota = amortizacion
            monto_cuota = capital_cuota + interes
        saldo = saldo - capital_cuota
        if ajustada:
            # Vuelve a repartir el saldo restante en partes iguales sobre los
            # períodos que faltan (incluyendo la última, igual que el cálculo
            # inicial de `amortizacion` sobre plazo_meses) para que las
            # próximas cuotas no ajustadas absorban el cambio de a poco en vez
            # de todo de golpe en la última.
            amortizacion = _amortizacion_constante(saldo, plazo_meses - numero)
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
