"""Cobertura de cas_client/formatting.py.

Sin dependencia de Qt ni de gRPC (funciones puras), igual que
tests/client/test_rbac_ui.py -- ver la nota de CLAUDE.md sobre el fixture
autouse `clean_db`, que igual corre para estos tests aunque no toquen la base.
"""

from datetime import datetime

from cas_client.formatting import (
    DISPLAY_DATE_PLACEHOLDER,
    fecha,
    fecha_a_iso,
    fecha_hora,
    gs,
    rate_percent,
)


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
    assert fecha_hora(datetime(2026, 8, 13, 7, 5)) == "13/08/2026 07:05"


def test_placeholder_matches_the_format_actually_parsed():
    assert DISPLAY_DATE_PLACEHOLDER == "DD/MM/AAAA"
    assert fecha_a_iso("01/02/2026") == "2026-02-01"  # día primero, no mes


def test_gs_and_rate_percent_are_unchanged_by_the_date_work():
    assert gs("1000000.00") == "1.000.000 Gs"
    assert rate_percent("0.24") == "24%"
