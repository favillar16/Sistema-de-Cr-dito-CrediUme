from typing import Callable

from PySide6.QtCore import QThread, Signal

# Every view keeps only a single `self._worker` attribute and reassigns it
# per call (see e.g. loans_view.py) -- fine for one-off calls, but a success
# handler that itself starts another call (e.g. LoansView._on_create_success
# chaining into _run_list() then _load_detail(), both of which reassign
# self._worker) drops the only Python reference to the *previous* worker
# while its QThread may not have fully wound down yet. PySide6 then garbage
# collects the still-finishing QThread, which Qt treats as a fatal error
# ("QThread: Destroyed while thread is still running") and aborts the whole
# process -- this was the crash reported when creating a new loan. Keeping a
# strong reference here, independent of any view's own attribute, until
# `finished` actually fires (which Qt only emits once run() has returned and
# the OS thread has stopped) closes that race for every call site at once.
_ACTIVE_WORKERS: set["AsyncWorker"] = set()


class AsyncWorker(QThread):
    """Runs a single callable off the UI thread and reports back via signals.

    Generalizes the _LoginWorker pattern from login_view.py so every gRPC
    call site doesn't need its own bespoke QThread subclass -- per ES-003 §5,
    every call from the UI thread must run on a worker thread with errors
    translated to a friendly message before reaching the view.
    """

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        fn: Callable,
        *args,
        error_translator: Callable[[Exception], str] | None = None,
        **kwargs,
    ):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._error_translator = error_translator or (lambda exc: str(exc))
        _ACTIVE_WORKERS.add(self)
        self.finished.connect(self._release)

    def _release(self) -> None:
        _ACTIVE_WORKERS.discard(self)
        self.deleteLater()

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 -- translated below, never re-raised
            self.failed.emit(self._error_translator(exc))
            return
        self.succeeded.emit(result)
