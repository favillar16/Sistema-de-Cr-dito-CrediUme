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
from PySide6.QtWidgets import QApplication  # noqa: E402

from cas_client import assets, theme  # noqa: E402
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
    # Every view-level button now gets an explicit style (theme.secondary_/
    # accent_/flat_button_style()), so this only needs to cover the one kind
    # of button the app doesn't construct directly: QMessageBox's built-in
    # Yes/No buttons (_on_deactivate, _on_mark_defaulted). Scoped with the
    # "QMessageBox QPushButton" descendant selector so it can't leak onto
    # unrelated buttons elsewhere (e.g. the sidebar's own per-button styles).
    app.setStyleSheet(
        "QMessageBox QPushButton { "
        f"background-color: {theme.APP_BACKGROUND}; color: {theme.TEXT_PRIMARY}; "
        f"border: 1px solid {theme.BORDER}; border-radius: 6px; padding: 6px 16px; "
        "font-weight: 600; }"
        f"QMessageBox QPushButton:hover {{ background-color: white; "
        f"border: 1px solid {theme.PRIMARY}; color: {theme.PRIMARY}; }}"
    )
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
