from datetime import datetime

import grpc
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from cas_client import theme
from cas_client.formatting import gs
from cas_client.grpc_client import ApiError, DashboardServiceClient
from cas_client.rbac_ui import tier_label
from cas_client.session import Session
from cas_client.widgets.async_worker import AsyncWorker
from cas_client.widgets.base_view import BaseView
from cas_client.widgets.card import section_label, stat_tile
from cas_client.widgets.responsive_grid import ResponsiveGrid

_LOAN_STATUS_TILES = (
    ("pending_loans_count", "Pendientes de aprobación", "PENDING", "PE"),
    ("approved_loans_count", "Aprobados (sin desembolsar)", "APPROVED", "AP"),
    ("active_loans_count", "Activos", "ACTIVE", "AC"),
    ("paid_loans_count", "Pagados", "PAID", "PG"),
    ("defaulted_loans_count", "Incumplidos", "DEFAULTED", "IN"),
    ("expired_loans_count", "Caducados", "EXPIRED", "CA"),
)


def _greeting() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "Buenos días"
    if 12 <= hour < 19:
        return "Buenas tardes"
    return "Buenas noches"


def _format_time(dt: datetime) -> str:
    hour12 = dt.hour % 12 or 12
    suffix = "a. m." if dt.hour < 12 else "p. m."
    return f"{hour12}:{dt.minute:02d} {suffix}"


def _friendly_message(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        if exc.code == grpc.StatusCode.PERMISSION_DENIED:
            return "No tiene permisos para ver las estadísticas del dashboard."
        if exc.code == grpc.StatusCode.UNAVAILABLE:
            return "No se pudo conectar con el servidor."
        return "No se pudieron cargar las estadísticas. Intente nuevamente."
    return f"No se pudo conectar con el servidor: {exc}"


class DashboardView(BaseView):
    """Pantalla de inicio: saludo + estadísticas agregadas de clientes/préstamos.

    Reemplaza el antiguo placeholder de bienvenida -- ver GetDashboardStats en
    dashboard_service.proto (no había ninguna RPC de agregados hasta ahora).
    """

    def __init__(
        self,
        client: DashboardServiceClient,
        session: Session,
        parent: QWidget | None = None,
    ):
        super().__init__(parent=parent)
        self._client = client
        self._session = session
        self._worker: AsyncWorker | None = None
        # Bumped on every _refresh_stats() call so a stale worker's callbacks
        # (a previous refresh still in flight when the user tabs back in,
        # see showEvent()) can tell they've been superseded and no-op instead
        # of overwriting a newer response or hiding the newer request's
        # progress bar -- ES-006 §3.5.
        self._refresh_generation = 0

        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        heading_column = QVBoxLayout()
        heading_column.setSpacing(2)
        self._heading_label = QLabel(f"{_greeting()} — Panel")
        self._heading_label.setStyleSheet(
            f"font-size: 22px; font-weight: 600; color: {theme.PRIMARY}; "
            f"font-family: {theme.HEADING_FONT_FAMILY};"
        )
        # Non-wrapping QLabels can get silently compressed below their own
        # text width by the layout under tight space (a recurring gotcha in
        # this codebase -- see login_view.py's wordmark for the full
        # explanation); Minimum pins sizeHint() as a hard floor instead.
        self._heading_label.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred
        )
        heading_column.addWidget(self._heading_label)
        self._updated_label = QLabel("")
        self._updated_label.setStyleSheet(
            f"font-size: 12px; color: {theme.TEXT_MUTED};"
        )
        heading_column.addWidget(self._updated_label)
        header_row.addLayout(heading_column, stretch=1)

        refresh_button = QPushButton("Actualizar")
        refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_button.setStyleSheet(theme.secondary_button_style())
        refresh_button.clicked.connect(self._refresh_stats)
        header_row.addWidget(refresh_button, alignment=Qt.AlignmentFlag.AlignTop)
        self.content_layout.addLayout(header_row)

        self._welcome_label = QLabel("")
        self._welcome_label.setStyleSheet(
            f"font-size: 14px; color: {theme.TEXT_MUTED};"
        )
        # A long username + role ("Sesión iniciada como ... · Nivel:
        # Administrador") can legitimately exceed the available width at the
        # app's minimum window size -- word wrap lets it flow to a second
        # line instead of getting clipped.
        self._welcome_label.setWordWrap(True)
        self.content_layout.addWidget(self._welcome_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        self.content_layout.addWidget(self._progress)

        self._error_label = QLabel("")
        self._error_label.setStyleSheet(f"color: {theme.ERROR}; font-size: 12px;")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        self.content_layout.addWidget(self._error_label)

        self.content_layout.addWidget(section_label("Clientes"))
        self._clients_grid = ResponsiveGrid(min_cell_width=220)
        self.content_layout.addWidget(self._clients_grid)

        self.content_layout.addWidget(section_label("Préstamos por estado"))
        # Wider than the other grids -- these captions ("Aprobados (sin
        # desembolsar)", etc.) are the longest on the dashboard, so a wider
        # minimum column keeps _ElidingLabel's "…" truncation rare in
        # normal window sizes instead of just safe when it happens.
        self._loans_grid = ResponsiveGrid(min_cell_width=260)
        self.content_layout.addWidget(self._loans_grid)

        self.content_layout.addWidget(section_label("Cartera"))
        self._portfolio_grid = ResponsiveGrid(min_cell_width=260)
        self.content_layout.addWidget(self._portfolio_grid)

        self.content_layout.addStretch()

        self._rebuild_tiles(stats=None)

    def set_user(self, username: str, role: str) -> None:
        self._welcome_label.setText(
            f"Sesión iniciada como {username} · Nivel: {tier_label(role)}"
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.update()
        if self._session.access_token:
            self._refresh_stats()

    def _refresh_stats(self) -> None:
        if not self._session.access_token:
            return
        self._refresh_generation += 1
        generation = self._refresh_generation
        self._heading_label.setText(f"{_greeting()} — Panel")
        self._error_label.hide()
        self._show_progress()
        self._worker = AsyncWorker(
            self._client.get_dashboard_stats,
            self._session.access_token,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(
            lambda stats: self._on_stats_loaded(stats, generation)
        )
        self._worker.failed.connect(
            lambda message: self._on_stats_failed(message, generation)
        )
        self._worker.finished.connect(lambda: self._hide_progress_for(generation))
        self._worker.start()

    def _hide_progress_for(self, generation: int) -> None:
        if generation == self._refresh_generation:
            self._hide_progress()

    def _show_progress(self) -> None:
        self._progress.setRange(0, 0)
        self._progress.show()

    def _hide_progress(self) -> None:
        # Toggling only visibility leaves the indeterminate busy-animation
        # timer running while hidden, which can leave a stale animation
        # frame (a light streak) baked into the parent's backing store when
        # the QStackedWidget switches pages right after. Parking the range
        # at (0, 1) while hidden stops that timer.
        self._progress.hide()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)

    def _rebuild_tiles(self, stats) -> None:
        """(Re)builds every stat tile from scratch rather than mutating an
        existing QLabel's text in place.

        Repeatedly calling setText() on long-lived tile widgets (the
        original approach here) turned out not to be reliably safe on this
        app's actual Windows target: after several refresh/navigate cycles,
        stray fragments of *previous* renders (old glyph edges, an old
        badge outline) would bleed through as faint ghosting -- a stale
        backing-store repaint issue, not the caption-wrap overlap this
        module used to have. update()/repaint() calls here previously
        tried to paper over that and weren't reliable enough (still
        reproduced after ~8 refresh cycles). Tearing down and recreating
        the tile widgets each time sidesteps the whole bug class: a freshly
        constructed QLabel has no prior paint history to bleed through.
        """
        self._clients_grid.clear()
        clients_total_frame, _ = stat_tile(
            "Total de clientes",
            accent_color=theme.PRIMARY,
            icon_text="T",
            value_text=str(stats.total_clients_count) if stats else "—",
        )
        self._clients_grid.add_widget(clients_total_frame)
        clients_active_frame, _ = stat_tile(
            "Clientes activos",
            accent_color=theme.SUCCESS,
            icon_text="A",
            value_text=str(stats.active_clients_count) if stats else "—",
        )
        self._clients_grid.add_widget(clients_active_frame)

        self._loans_grid.clear()
        for field, label, status_key, icon_text in _LOAN_STATUS_TILES:
            accent_color, _fg = theme.LOAN_STATUS_COLORS[status_key]
            frame, _ = stat_tile(
                label,
                accent_color=accent_color,
                icon_text=icon_text,
                value_text=str(getattr(stats, field)) if stats else "—",
            )
            self._loans_grid.add_widget(frame)

        self._portfolio_grid.clear()
        disbursed_frame, _ = stat_tile(
            "Cartera desembolsada (Gs)",
            accent_color=theme.PRIMARY,
            icon_text="₲",
            value_text=gs(stats.total_disbursed) if stats else "—",
        )
        self._portfolio_grid.add_widget(disbursed_frame)
        outstanding_frame, _ = stat_tile(
            "Saldo pendiente de cobro (Gs)",
            accent_color=theme.ACCENT,
            icon_text="₲",
            value_text=gs(stats.total_outstanding_balance) if stats else "—",
        )
        self._portfolio_grid.add_widget(outstanding_frame)

    def _on_stats_loaded(self, stats, generation: int) -> None:
        if generation != self._refresh_generation:
            return  # respuesta de un refresh superado por uno más nuevo
        self._rebuild_tiles(stats)
        self._updated_label.setText(f"Actualizado a las {_format_time(datetime.now())}")

    def _on_stats_failed(self, message: str, generation: int) -> None:
        if generation != self._refresh_generation:
            return
        self._error_label.setText(message)
        self._error_label.show()
