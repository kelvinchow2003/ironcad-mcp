"""Single dedicated STA worker thread for all COM / IronCAD interaction.

Why (spec Section 2.1)
----------------------
``python_ironcad`` drives IronCAD through apartment-threaded (STA) COM objects
(via ``comtypes``; ``pywin32``/``pythoncom`` provides the apartment init here).
Touching those objects from arbitrary asyncio / threadpool threads raises
``CoInitialize has not been called`` or cross-apartment marshaling failures.

Design:

* One long-lived worker thread, created at startup.
* On that thread: ``pythoncom.CoInitialize()`` once, then a loop that pulls
  callables off a thread-safe queue, runs them, and returns the result or
  exception through a ``concurrent.futures.Future``.
* Every derived IronCAD reference (``IZBaseApp``, ``ActiveDoc``, catalog
  objects, ...) is created and used **only** on this thread.
* :meth:`ComWorker.run` / :func:`run_on_com` is the single entry point; route
  *every* COM interaction through it -- no exceptions.
* On shutdown: release refs and ``pythoncom.CoUninitialize()``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
from typing import Any, Callable, Optional

from .logging_setup import get_logger, redirect_stdout_to_stderr

_logger = get_logger()

# Sentinel pushed onto the queue to stop the worker loop.
_SHUTDOWN = object()


class ComWorkerError(RuntimeError):
    """Raised when the worker thread is not available to service a call."""


class ComWorker:
    """Owns the STA thread and marshals callables onto it."""

    def __init__(self, name: str = "ironcad-com") -> None:
        self._name = name
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._init_error: Optional[BaseException] = None
        self._started = False
        self._stopping = False

    # ---- lifecycle -----------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=30)
        if self._init_error is not None:
            raise ComWorkerError(f"COM worker failed to initialize: {self._init_error!r}")
        if not self._ready.is_set():
            raise ComWorkerError("COM worker did not become ready within 30s")
        self._started = True
        _logger.info("COM worker thread started (STA, CoInitialize done)")

    def stop(self, timeout: float = 10.0) -> None:
        if not self._started or self._stopping:
            return
        self._stopping = True
        self._queue.put((_SHUTDOWN, None, None))
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._started = False
        _logger.info("COM worker thread stopped")

    @property
    def running(self) -> bool:
        return self._started and self._thread is not None and self._thread.is_alive()

    # ---- the worker thread ---------------------------------------------

    def _loop(self) -> None:
        import pythoncom  # imported on the worker thread

        try:
            # STA apartment for this thread. comtypes objects created here will
            # share this apartment.
            pythoncom.CoInitialize()
        except BaseException as exc:  # noqa: BLE001
            self._init_error = exc
            self._ready.set()
            return

        self._ready.set()
        try:
            while True:
                fn, future, _ = self._queue.get()
                if fn is _SHUTDOWN:
                    break
                if future.cancelled():  # caller gave up already
                    continue
                try:
                    # Guard against native prints leaking to stdout (spec 2.2).
                    with redirect_stdout_to_stderr():
                        result = fn()
                    future.set_result(result)
                except BaseException as exc:  # noqa: BLE001 - propagate to caller
                    future.set_exception(exc)
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass

    # ---- submission ----------------------------------------------------

    def submit(self, fn: Callable[[], Any]) -> "concurrent.futures.Future[Any]":
        """Queue *fn* to run on the COM thread; return a Future for the result."""
        if not self.running:
            raise ComWorkerError("COM worker is not running")
        future: "concurrent.futures.Future[Any]" = concurrent.futures.Future()
        self._queue.put((fn, future, None))
        return future

    def run_sync(self, fn: Callable[[], Any], timeout: float | None = 60.0) -> Any:
        """Blocking helper for non-async contexts (scripts, tests)."""
        return self.submit(fn).result(timeout=timeout)

    async def run(self, fn: Callable[[], Any]) -> Any:
        """Async entry point used by tool handlers: ``await worker.run(fn)``."""
        future = self.submit(fn)
        return await asyncio.wrap_future(future)


# ---- module-level singleton + convenience ------------------------------

_worker: Optional[ComWorker] = None


def get_worker() -> ComWorker:
    global _worker
    if _worker is None:
        _worker = ComWorker()
    return _worker


def start_worker() -> ComWorker:
    worker = get_worker()
    worker.start()
    return worker


def stop_worker() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()


async def run_on_com(fn: Callable[[], Any]) -> Any:
    """Route a callable onto the single COM thread and await its result.

    This is the ``await run_on_com(...)`` helper the spec mandates for every
    COM interaction.
    """
    return await get_worker().run(fn)
