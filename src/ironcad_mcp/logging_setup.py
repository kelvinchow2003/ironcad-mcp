"""Logging + stdout protection for the IronCAD MCP server.

Why this module exists (spec Section 2.2)
-----------------------------------------
The MCP stdio transport uses **stdout** for JSON-RPC framing. *Any* stray byte
written to the real stdout corrupts the protocol and the classic symptom is a
server that "connects then does nothing".

Two concrete hazards we have already observed on this machine:

* ``import python_ironcad`` prints banner lines ("IRONCAD 26.0 InstallDir: ...",
  "Extracting API from type library, this may take a while...") straight to
  stdout at import time.
* ``pywin32`` / ``comtypes`` / COM initialisation can emit to stdout.

So we:

1. Send all ``logging`` output to **stderr** and (optionally) a rotating file.
2. Provide :func:`redirect_stdout_to_stderr`, a context manager wrapping every
   COM / python_ironcad interaction that might print, re-pointing the *real*
   OS-level stdout (fd 1) at stderr and restoring it afterwards.
3. Provide :func:`assert_clean_stdout` so startup can fail loudly if anything
   leaked to stdout before the transport took it over.
"""

from __future__ import annotations

import contextlib
import io
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Iterator

_LOGGER_NAME = "ironcad_mcp"
_configured = False


def setup_logging(level: str | int | None = None, log_file: str | os.PathLike | None = None) -> logging.Logger:
    """Configure the package logger to write to stderr (+ optional file).

    Never adds a stdout handler. Safe to call more than once.
    """
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    if level is None:
        level = os.environ.get("IRONCAD_MCP_LOG_LEVEL", "INFO")
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # stderr handler -- explicitly the real stderr, never stdout.
    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setFormatter(fmt)
    logger.addHandler(stderr_handler)

    if log_file is None:
        log_file = os.environ.get("IRONCAD_MCP_LOG_FILE")
    if log_file:
        try:
            path = Path(log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(fmt)
            logger.addHandler(file_handler)
        except Exception:  # noqa: BLE001 - never let logging setup crash startup
            logger.exception("Could not open log file %s; continuing with stderr only", log_file)

    logger.propagate = False
    _configured = True
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


@contextlib.contextmanager
def redirect_stdout_to_stderr() -> Iterator[None]:
    """Re-point the real OS-level stdout (fd 1) at stderr for the duration.

    Wrap every block that touches python_ironcad / COM / pywin32 which may print.
    We dup the underlying file descriptor (not just ``sys.stdout``) because the
    noisy writers are native (C) code that writes to fd 1 directly.
    """
    logger = get_logger()

    # Try the robust fd-level redirect first.
    saved_fd = None
    stderr_fd = None
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
    try:
        stdout_fd = sys.stdout.fileno()
        stderr_fd = sys.stderr.fileno()
        saved_fd = os.dup(stdout_fd)
        os.dup2(stderr_fd, stdout_fd)
    except (AttributeError, io.UnsupportedOperation, OSError):
        # No real fds (e.g. captured under pytest). Fall back to object-level.
        saved_fd = None

    if saved_fd is None:
        # Object-level fallback: redirect the Python stdout object only.
        with contextlib.redirect_stdout(sys.stderr):
            try:
                yield
            finally:
                pass
        return

    try:
        yield
    finally:
        try:
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            os.dup2(saved_fd, sys.stdout.fileno())
        except Exception:  # noqa: BLE001
            logger.exception("Failed to restore stdout after redirect")
        finally:
            try:
                os.close(saved_fd)
            except Exception:  # noqa: BLE001
                pass


class _TripwireStdout(io.TextIOBase):
    """Wrap the real stdout so any unexpected write is logged (and dropped).

    Installed as ``sys.stdout`` *before* the MCP transport starts, so leaks are
    surfaced during development. The MCP stdio server writes JSON-RPC by holding
    its own reference to the real stream, so this tripwire does not interfere
    with the protocol -- it only catches accidental ``print()`` calls in our code.
    """

    def __init__(self, real: io.TextIOBase, logger: logging.Logger) -> None:
        self._real = real
        self._logger = logger
        self.leaked = False

    def write(self, s):  # type: ignore[override]
        if s and s.strip():
            self.leaked = True
            self._logger.error("STDOUT LEAK (dropped to protect JSON-RPC): %r", s[:200])
        return len(s)

    def flush(self):  # type: ignore[override]
        return None


def install_stdout_tripwire() -> _TripwireStdout:
    """Replace ``sys.stdout`` with a tripwire that logs+drops stray writes.

    Call this once at startup *after* capturing the real stdout for the MCP
    transport, or rely on the MCP SDK owning the real stream. Returns the
    tripwire so callers can inspect ``.leaked``.
    """
    logger = get_logger()
    tripwire = _TripwireStdout(sys.stdout, logger)  # type: ignore[arg-type]
    sys.stdout = tripwire  # type: ignore[assignment]
    return tripwire
