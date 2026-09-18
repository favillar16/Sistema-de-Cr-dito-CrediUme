"""Diagnóstico del ticket de 80 mm sobre la impresora térmica real de la caja.

Se corre EN LA PC DE LA CAJA, desde la raíz del repo y con el venv activado:

    python scripts/diagnostico_impresora_ticket.py

`tests/client/test_ticket_escpos.py` prueba los bytes ESC/POS del ticket sin
Windows; lo que ese test no puede ver es si esos bytes efectivamente llegan a
la impresora física. Este script imprime un ticket de prueba por el mismo
camino que usan `CashView` y `LoansView` -- `printing.print_ticket()`, que
manda los bytes de `escpos.ticket_cobro_escpos()` en crudo (datatype "RAW")
a `TICKET_PRINTER_NAME` (o la predeterminada de Windows si no está
configurada), sin pasar por el sistema de páginas de Windows en absoluto.

Por qué en crudo y no con QPrinter/QTextDocument (Qt): confirmado en la caja
real el 2026-09-18 que ni el driver instalado ("Generic / Text Only") ni los
genéricos "Microsoft Virtual Print Class Driver"/"Universal Print Class
Driver" respetan un tamaño de página personalizado -- los tres fuerzan A4 sin
avisar. Ver el docstring de cas_client/printing.py para el detalle completo.
"""

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import win32print  # noqa: E402

from cas_client import config, printing  # noqa: E402


def _titulo(texto: str) -> None:
    print(f"\n{texto}\n{'-' * len(texto)}")


class _FakeTimestamp:
    def __init__(self, momento: datetime) -> None:
        self._momento = momento

    def ToDatetime(self) -> datetime:
        return self._momento


class _FakeClient:
    first_name = "Prueba"
    last_name = "Diagnóstico"
    national_id = "0.000.000"
    phone_number = "(000) 000 000"
    address = "-"


class _FakeLoan:
    id = "00000000-0000-0000-0000-000000000000"


class _FakePayment:
    status = "ACTIVE"
    amount_paid = "100000.00"
    total_paid = "100000.00"
    remaining_balance = "900000.00"
    covered_installments = [1]
    total_installments = 12
    payment_method = "EFECTIVO"
    transfer_reference = ""
    recorded_by_name = "Diagnóstico"
    recorded_by_national_id = "0.000.000"

    def __init__(self) -> None:
        self.paid_at = _FakeTimestamp(datetime.now())


def main() -> int:
    _titulo("Impresoras disponibles (win32print)")
    impresoras = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL)
    if not impresoras:
        print("  Ninguna. Windows no reporta ninguna impresora instalada.")
    predeterminada = win32print.GetDefaultPrinter()
    for _flags, _server, nombre, _comentario in impresoras:
        marcador = " (predeterminada)" if nombre == predeterminada else ""
        print(f"  - {nombre}{marcador}")

    print(f"\nTICKET_PRINTER_NAME (cas_client/.env) = {config.TICKET_PRINTER_NAME!r}")
    if not config.TICKET_PRINTER_NAME:
        print(
            "  Sin configurar: print_ticket() usará la impresora predeterminada\n"
            "  de Windows. Si esa NO es la térmica, configurar TICKET_PRINTER_NAME\n"
            "  en cas_client/.env con el nombre exacto de la lista de arriba."
        )

    _titulo("Imprimiendo (printing.print_ticket(), el mismo camino que la app)")
    try:
        printing.print_ticket(_FakeLoan(), _FakeClient(), _FakePayment())
    except printing.PrinterNotFoundError as exc:
        print(f"  [FALLA] {exc}")
        return 1
    except printing.PrinterError as exc:
        print(f"  [FALLA] {exc}")
        return 1

    print("  [OK] El trabajo se mandó sin error a la API de impresión de Windows.")
    print(
        "\nEsto solo confirma que Windows aceptó el trabajo -- revisar el papel\n"
        "físico para confirmar que salió con el formato correcto (80 mm, corte,\n"
        "acentos legibles). Si el ancho de columna se ve mal (texto que da la\n"
        "vuelta antes de tiempo, o pegado al borde derecho), ajustar\n"
        "escpos._COLUMNAS (por defecto 48, el valor típico de Font A a 80 mm/\n"
        "203dpi) y volver a correr este script."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
