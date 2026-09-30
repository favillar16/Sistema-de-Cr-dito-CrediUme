"""BR-LOAN-018: cancelación del préstamo en un solo pago con descuento.

Funciones puras, sin Qt ni gRPC, compartidas por las dos pantallas que cobran
(la tarjeta de cobro de CashView y "Registrar pago" de LoansView). Son dos
entradas a la misma regla, y la forma barata de romperla es que cada una arme
su propio total y una se olvide del descuento o de la mora -- mismo motivo por
el que BR-LOAN-017 exige que las dos muestren cuota + mora antes de confirmar.

Todo lo de acá es para **mostrar** antes de confirmar: el servidor recalcula el
saldo con la fila bloqueada, cobra la mora completa y vuelve a validar el
descuento. Si discrepan, manda el servidor.
"""

from decimal import Decimal, InvalidOperation

from cas_client.formatting import gs

# Número de cuota "ficticio" que identifica la opción de pago total en el
# itemData del combo de cuotas. Negativo para que nunca choque con una cuota
# real (1..plazo) ni con el 0 del modo libre de RecordPayment; nunca viaja al
# servidor, que recibe `pay_in_full=True` en su lugar.
PAY_IN_FULL = -1

_ZERO = Decimal("0")


def _dec(value: str) -> Decimal:
    try:
        return Decimal(value or "0")
    except (InvalidOperation, ValueError):
        return _ZERO


def payoff_item(schedule) -> tuple[str, tuple[int, str, str]] | None:
    """Etiqueta e itemData de la opción "Pago total" para el combo de cuotas.

    `schedule` es un GetAmortizationScheduleResponse. El itemData tiene la
    misma forma de tres elementos que las cuotas -- (número, monto, mora) -- a
    propósito: el combo ya se desempaqueta así en cuatro lugares, y agregarle
    un cuarto elemento es exactamente el error que tests/client/test_cobro_con_
    mora.py atrapó una vez.

    None si no queda nada por cobrar.
    """
    balance = _dec(schedule.remaining_balance)
    if balance <= _ZERO:
        return None
    late_fee = sum((_dec(cuota.late_fee) for cuota in schedule.installments), _ZERO)
    label = f"Pago total del préstamo · saldo {gs(str(balance))}"
    if late_fee > _ZERO:
        label += f" + mora {gs(str(late_fee))}"
    return label, (PAY_IN_FULL, str(balance), str(late_fee))


def is_pay_in_full(item_data) -> bool:
    return item_data is not None and item_data[0] == PAY_IN_FULL


def amount_to_collect(amount: str, late_fee: str, discount: str = "") -> Decimal:
    """Lo que el operador tiene que recibir: monto - descuento + mora.

    Un descuento inválido o fuera de rango se toma como cero para el total
    mostrado; el error se informa aparte (`discount_error`).
    """
    monto = _dec(amount)
    descuento = _dec(discount)
    if descuento < _ZERO or descuento >= monto:
        descuento = _ZERO
    return monto - descuento + _dec(late_fee)


def discount_error(discount: str, balance: str) -> str | None:
    """Mensaje para el operador si el descuento no se puede aplicar; None si
    está bien (vacío es "sin descuento", y está bien).

    Mismo límite que el servidor: tiene que quedar algo del saldo por cobrar.
    """
    if not discount:
        return None
    descuento = _dec(discount)
    saldo = _dec(balance)
    if descuento < _ZERO:
        return "El descuento no puede ser negativo."
    if descuento >= saldo:
        return f"El descuento debe ser menor que el saldo pendiente ({gs(str(saldo))})."
    return None


def history_amount_text(entry) -> str:
    """Celda "Monto" del historial de cobros (BR-LOAN-016).

    El monto es lo imputado al cronograma -- en una cancelación, el saldo
    entero --, así que si hubo descuento se aclara al lado: si no, la fila
    diría que el cliente pagó más de lo que entregó.
    """
    texto = gs(entry.amount)
    if _dec(getattr(entry, "discount_amount", "")) > _ZERO:
        texto += f" (desc. {gs(entry.discount_amount)})"
    return texto


def breakdown_text(amount: str, late_fee: str, discount: str, pay_in_full: bool) -> str:
    """Desglose que se le lee al cliente; "" si no hay nada que desglosar.

    Sin pago total es el mismo texto de BR-LOAN-017 que ya mostraban las dos
    pantallas.
    """
    hay_mora = _dec(late_fee) > _ZERO
    if not pay_in_full:
        if not hay_mora:
            return ""
        return (
            f"Incluye {gs(late_fee)} de mora por atraso, además de la cuota de "
            f"{gs(amount)}."
        )
    partes = [f"Saldo pendiente {gs(amount)}"]
    if discount_error(discount, amount) is None and _dec(discount) > _ZERO:
        partes.append(f"− descuento {gs(discount)}")
    if hay_mora:
        partes.append(f"+ mora {gs(late_fee)}")
    total = amount_to_collect(amount, late_fee, discount)
    return (
        " ".join(partes)
        + f" = {gs(str(total))}. Cancela el préstamo; la mora no admite descuento."
    )
