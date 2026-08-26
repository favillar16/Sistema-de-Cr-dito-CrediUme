"""Cobertura de cas_client/formatting.py.

Sin dependencia de Qt ni de gRPC (funciones puras), igual que
tests/client/test_rbac_ui.py -- ver la nota de CLAUDE.md sobre el fixture
autouse `clean_db`, que igual corre para estos tests aunque no toquen la base.
"""

from datetime import date, datetime

from cas_client.formatting import (
    DISPLAY_DATE_PLACEHOLDER,
    es_fecha_valida,
    fecha,
    fecha_a_iso,
    fecha_hora,
    gs,
    rate_percent,
    rate_percent_mensual,
)


def test_rate_percent_mensual_converts_the_nominal_annual_rate():
    """La tasa fija vigente (45% anual) es el 3,75% mensual que declaran el
    Pagaré y el Contrato -- amortization.py cobra tasa_anual / 12 por
    período."""
    assert rate_percent_mensual("0.45") == "3,75%"
    assert rate_percent_mensual("0.24") == "2%"


def test_rate_percent_mensual_uses_a_decimal_comma():
    """Se imprime en documentos legales en español, donde "3.75%" se lee como
    separador de miles."""
    assert "," in rate_percent_mensual("0.45")
    assert "." not in rate_percent_mensual("0.45")


def test_rate_percent_mensual_rounds_rates_that_do_not_divide_evenly():
    """10% anual / 12 es periódico: sin redondeo imprimiría 28 dígitos."""
    assert rate_percent_mensual("0.10") == "0,833%"


def test_rate_percent_mensual_leaves_empty_and_unparseable_values_untouched():
    assert rate_percent_mensual("") == ""
    assert rate_percent_mensual("no es una tasa") == "no es una tasa"


def test_fecha_renders_wire_dates_as_day_month_year():
    assert fecha("2026-10-09") == "09/10/2026"
    assert fecha("2026-01-31") == "31/01/2026"


def test_fecha_leaves_empty_and_unparseable_values_untouched():
    """Se renderiza en labels y celdas de tabla de solo lectura: fallar duro
    ahí tumbaría la fila entera por un tema de formato."""
    assert fecha("") == ""
    assert fecha("no es una fecha") == "no es una fecha"


def test_fecha_a_iso_converts_what_the_user_typed_to_wire_format():
    assert fecha_a_iso("09/10/2026") == "2026-10-09"
    assert fecha_a_iso("  09/10/2026  ") == "2026-10-09"


def test_fecha_a_iso_passes_through_already_iso_values():
    """Un valor que fecha() no pudo formatear vuelve tal cual al servidor en
    vez de corromperse en el camino de ida."""
    assert fecha_a_iso("2026-10-09") == "2026-10-09"


def test_fecha_a_iso_leaves_invalid_input_for_the_server_to_reject():
    assert fecha_a_iso("31/31/2026") == "31/31/2026"
    assert fecha_a_iso("") == ""


def test_fecha_round_trips_through_fecha_a_iso():
    assert fecha_a_iso(fecha("2026-02-28")) == "2026-02-28"


def test_fecha_hora_uses_the_same_date_format_plus_a_24h_clock():
    """El formato es DD/MM/AAAA HH:MM. El instante se toma ya en hora local
    (una zona fija, no la de la máquina) para probar sólo el formateo -- la
    conversión desde el UTC del servidor tiene sus propios tests más abajo."""
    from datetime import datetime as _datetime

    local = _datetime(2026, 8, 13, 7, 5).astimezone()
    assert fecha_hora(local) == "13/08/2026 07:05"


def test_placeholder_matches_the_format_actually_parsed():
    assert DISPLAY_DATE_PLACEHOLDER == "DD/MM/AAAA"
    assert fecha_a_iso("01/02/2026") == "2026-02-01"  # día primero, no mes


def test_gs_and_rate_percent_are_unchanged_by_the_date_work():
    assert gs("1000000.00") == "1.000.000 Gs"
    assert rate_percent("0.24") == "24%"


# ---- Hora local vs UTC (a_hora_local / fecha_hora) ------------------------


def test_a_hora_local_reads_a_naive_datetime_as_utc():
    """Todo datetime que llega del servidor sale de Timestamp.ToDatetime(),
    que devuelve un naive **en UTC**. Interpretarlo como hora local mostraba
    la hora equivocada en la caja y en el comprobante que se entrega al
    deudor."""
    from datetime import timezone

    from cas_client.formatting import a_hora_local

    local = a_hora_local(datetime(2026, 8, 13, 12, 0))
    assert local.tzinfo is not None
    assert local.astimezone(timezone.utc).replace(tzinfo=None) == datetime(
        2026, 8, 13, 12, 0
    )


def test_a_hora_local_keeps_an_aware_datetime_at_the_same_instant():
    from datetime import timedelta, timezone

    from cas_client.formatting import a_hora_local

    origen = datetime(2026, 8, 13, 12, 0, tzinfo=timezone(timedelta(hours=5)))
    assert a_hora_local(origen) == origen


def test_fecha_hora_renders_the_local_wall_clock_of_a_utc_instant():
    """La conversion no se puede fijar contra una hora literal (el resultado
    depende de la zona de la maquina que corre el test), asi que se compara
    contra la misma conversion hecha a mano -- lo que si se fija es que ya no
    imprime el naive UTC crudo."""
    from datetime import timezone

    from cas_client.formatting import a_hora_local

    instante = datetime(2026, 8, 13, 12, 0)
    esperado = a_hora_local(instante).strftime("%d/%m/%Y %H:%M")
    assert fecha_hora(instante) == esperado
    assert fecha_hora(instante.replace(tzinfo=timezone.utc)) == esperado


def test_fecha_a_iso_accepts_the_display_format_with_other_separators():
    """El placeholder pide DD/MM/AAAA, pero el guion y el punto son igual de
    habituales al tipear (y el teclado numérico no tiene la barra a mano).
    Es la misma fecha, escrita distinto -- no un formato nuevo."""
    assert fecha_a_iso("09-10-2026") == "2026-10-09"
    assert fecha_a_iso("09.10.2026") == "2026-10-09"


def test_fecha_a_iso_never_reads_an_iso_value_as_day_first():
    """La guarda que hace segura la tolerancia de separadores de arriba: si
    "2026-10-09" se normalizara antes de probar ISO, se leería como día 2026 y
    se rompería."""
    assert fecha_a_iso("2026-10-09") == "2026-10-09"
    assert fecha_a_iso("2026/10/09") == "2026-10-09"


def test_es_fecha_valida_accepts_what_fecha_a_iso_can_convert():
    for valor in ("09/10/2026", "9/10/2026", "09-10-2026", "2026-10-09"):
        assert es_fecha_valida(valor), valor


def test_es_fecha_valida_rejects_what_the_server_would_reject():
    """Existe para avisar en DD/MM/AAAA antes de la llamada: el mensaje del
    servidor nombra el formato de cable (AAAA-MM-DD), que no es el que el
    formulario pide."""
    assert not es_fecha_valida("")
    assert not es_fecha_valida("   ")
    assert not es_fecha_valida("31/02/2026")  # día inexistente
    assert not es_fecha_valida("31/31/2026")
    assert not es_fecha_valida("mañana")


def test_es_fecha_valida_agrees_with_fecha_a_iso():
    """Invariante que sostiene el patrón de las vistas (validar y recién
    después convertir): es_fecha_valida() es exactamente "fecha_a_iso()
    produjo una fecha ISO real"."""
    for valor in ("09/10/2026", "09-10-2026", "2026-10-09", "31/02/2026", "mañana", ""):
        try:
            date.fromisoformat(fecha_a_iso(valor))
            convertible = True
        except ValueError:
            convertible = False
        assert es_fecha_valida(valor) is convertible, valor
