import os
import sys

# Qt 6.5+ auto-adapts unstyled widgets (QPushButton, QHeaderView, QTableWidget
# headers) to the Windows light/dark setting. This app has no dark theme of
# its own -- every view paints explicit white card/table backgrounds -- so on
# a machine with Windows dark mode enabled, that auto-dark text becomes
# invisible against our white. `darkmode=0` is the "windows" platform
# plugin's documented option to disable that adaptation entirely (must be set
# before QApplication is constructed). QStyleHints.setColorScheme() would be
# the newer API for this but isn't available before Qt 6.8 (this project
# pins PySide6 6.7).
#
# Condicionado a win32 y a que nadie lo haya fijado ya: forzar el plugin
# "windows" en otra plataforma impide que Qt arranque, y pisar un valor
# explícito rompe el renderizado sin ventana (QT_QPA_PLATFORM=offscreen) que
# se usa para revisar las vistas. Antes se asignaba de forma incondicional.
if sys.platform == "win32" and not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "windows:darkmode=0"

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from cas_client import assets, config, theme  # noqa: E402
from cas_client.grpc_client import ConfigurationError  # noqa: E402
from cas_client.main_window import MainWindow  # noqa: E402

# Launched via `python -m cas_client.main` (ES-004's bare-metal deployment
# model has no bundled .exe), Windows otherwise groups the running process
# under python.exe's own taskbar identity/icon instead of this app's --
# giving it an explicit AppUserModelID makes Windows treat it as its own
# distinct app for taskbar icon/grouping purposes. Must be set before any
# window is shown; harmless no-op on non-Windows platforms.
if sys.platform == "win32":
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "CredimedUme.CAS.DesktopClient"
        )
    except (AttributeError, OSError):
        pass


def main() -> None:
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(assets.APP_ICON_ICO))
    # Hoja global: diálogos estándar, barras de progreso/scroll y tooltip --
    # lo que las vistas no construyen ellas mismas (ver theme.app_stylesheet).
    # theme.tooltip_style() va dentro a propósito: el tooltip es una ventana
    # top-level, no un hijo del botón, así que sólo la hoja de la aplicación
    # lo alcanza. Sin él sale como un recuadro negro.
    app.setStyleSheet(theme.app_stylesheet())  # incluye tooltip_style()
    try:
        window = MainWindow()
    except ConfigurationError as exc:
        # MainWindow construye los cuatro *ServiceClient (y por lo tanto los
        # canales) antes de mostrar nada, así que un error de configuración
        # aparece acá, no dentro de una vista con su Toast. Sin este diálogo
        # el operador vería un traceback en una consola que el .vbs de
        # arranque ni siquiera abre.
        QMessageBox.critical(
            None,
            "Configuración del cliente",
            f"{exc}\n\nArchivo de configuración: cas_client\\.env\n"
            f"Servidor configurado: {config.GRPC_SERVER_HOST}:{config.GRPC_PORT}",
        )
        sys.exit(1)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
