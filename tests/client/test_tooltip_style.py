"""Los tooltips tienen que tener color propio, no el de la paleta del sistema.

La entidad reportó que al dejar el cursor sobre un botón aparecía "un cuadro
negro" en lugar del texto de ayuda. La causa: `QToolTip` era la única clase de
widget de la app que nunca recibía un `color:`/`background-color:` explícito,
así que caía en la paleta del sistema -- la misma trampa que `theme.py`
documenta para los botones planos y los combos, y que ya había producido
valores invisibles en el resumen del préstamo.

`QT_QPA_PLATFORM=windows:darkmode=0` (cas_client/main.py) no alcanza: desactiva
la adaptación automática de Qt para los widgets comunes, no la del popup del
tooltip, que es una ventana top-level aparte.

Por esa misma razón la regla **tiene que estar en la hoja de la aplicación**:
un `setStyleSheet` sobre el botón no llega al tooltip, porque el tooltip no es
hijo del botón. Estos tests verifican las dos cosas -- que la regla exista con
sus dos colores, y que main.py la incluya en la hoja global.
"""

import inspect

from cas_client import main, theme


def test_el_tooltip_define_texto_y_fondo():
    estilo = theme.tooltip_style()

    assert "QToolTip" in estilo
    # Las dos ramas: con una sola, la que falte la sigue poniendo el sistema,
    # que es exactamente el bug (texto negro sobre fondo negro).
    assert "color:" in estilo
    assert "background-color:" in estilo


def test_el_tooltip_no_usa_lime_de_fondo():
    """ACCENT es color de fondo, pero no de este fondo: el tooltip lleva texto
    oscuro sobre blanco. Lime con texto oscuro encima da menos contraste que
    el blanco y no aporta nada en un cuadro de ayuda."""
    assert theme.ACCENT not in theme.tooltip_style()


def test_la_hoja_de_la_aplicacion_incluye_el_tooltip():
    """En la hoja global, no en una vista: un tooltip es una ventana
    top-level y no lo alcanza el stylesheet del widget que lo dispara."""
    fuente = inspect.getsource(main.main)

    assert "setStyleSheet" in fuente
    assert "tooltip_style()" in fuente
