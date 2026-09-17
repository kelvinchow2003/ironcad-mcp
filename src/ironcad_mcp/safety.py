"""Safety: mode gating, backups, and name resolution (spec Section 7).

* Mode flag ``IRONCAD_MCP_MODE = read_only | read_write`` (default read_only).
  In read-only, every Tier C/D/E write must refuse via :func:`require_write_mode`.
* Backup-before-write: before any mutation, save a timestamped copy to
  ``IRONCAD_MCP_BACKUP_DIR``. If backup fails, abort the write.
* Fail-safe name resolution: ambiguous / missing names refuse rather than guess.

The actual "save a backup copy" call depends on ICAPI save methods discovered in
M1; :func:`backup_active_doc` is written against that interface and will be
finalized once API_NOTES.md records the concrete save-copy call.
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from .logging_setup import get_logger

_logger = get_logger()

MODE_READ_ONLY = "read_only"
MODE_READ_WRITE = "read_write"


class WriteRefused(RuntimeError):
    """Raised when a write is attempted in read-only mode."""


class NameResolutionError(RuntimeError):
    """Raised when a part name is missing or ambiguous."""


def current_mode() -> str:
    mode = os.environ.get("IRONCAD_MCP_MODE", MODE_READ_ONLY).strip().lower()
    return MODE_READ_WRITE if mode == MODE_READ_WRITE else MODE_READ_ONLY


def is_read_write() -> bool:
    return current_mode() == MODE_READ_WRITE


def require_write_mode(action: str) -> None:
    """Refuse a write unless read_write mode is enabled."""
    if not is_read_write():
        raise WriteRefused(
            f"Refused: '{action}' is a write, but IRONCAD_MCP_MODE is "
            f"'{current_mode()}'. Set IRONCAD_MCP_MODE=read_write (and restart "
            f"the server) to enable building. This protects your real files."
        )


def backup_dir() -> Optional[str]:
    d = os.environ.get("IRONCAD_MCP_BACKUP_DIR")
    return d.strip() if d else None


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def backup_active_doc(doc_path: str | None) -> Optional[str]:
    """Back up the active document before a mutation, via a plain FILE COPY.

    We deliberately do NOT use IronCAD's ``SaveAsCopy`` for backups: it pops
    modal dialogs (e.g. on scenes with linked catalog parts, or bad enum args)
    that irrecoverably wedge the STA COM worker thread (see API_NOTES §8).

    Semantics:
      * If ``doc_path`` is a real on-disk ``.ics`` file (a document the user has
        saved), copy it to ``IRONCAD_MCP_BACKUP_DIR`` with a timestamped name and
        return the backup path. This snapshots the last-saved version before the
        first Claude mutation (Claude never auto-saves). If the copy fails, abort.
      * If ``doc_path`` is not an existing file (a never-saved / new scratch
        scene), there is nothing on disk to lose — return ``None`` and allow the
        write.

    Returns the backup path, or ``None`` when the document is unsaved.
    """
    if not doc_path or not os.path.isfile(doc_path):
        _logger.info("No on-disk file for active document (%r); skipping backup "
                     "(nothing to lose).", doc_path)
        return None

    d = backup_dir()
    if not d:
        raise WriteRefused(
            "Refused: the active document is a saved file but no "
            "IRONCAD_MCP_BACKUP_DIR is configured, so no backup can be taken "
            "before writing. Set it (see README) and restart."
        )
    os.makedirs(d, exist_ok=True)
    base = os.path.splitext(os.path.basename(doc_path))[0]
    safe_name = "".join(c for c in base if c.isalnum() or c in "_- ").strip() or "active"
    dst = os.path.join(d, f"{safe_name}_backup_{_timestamp()}.ics")
    try:
        shutil.copy2(doc_path, dst)
    except Exception as exc:  # noqa: BLE001
        raise WriteRefused(f"Aborting write: backup copy failed ({exc}).") from exc
    _logger.info("Backup copied: %s -> %s", doc_path, dst)
    return dst


@dataclass
class NamedItem:
    index: int
    name: str
    obj: Any


def resolve_by_name(
    items: Iterable[NamedItem],
    name: str,
    index_fallback: Optional[int] = None,
) -> NamedItem:
    """Resolve a part by name (primary) with index fallback (spec Section 6).

    Refuses on missing or ambiguous names rather than guessing.
    """
    items = list(items)
    exact = [it for it in items if it.name == name]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise NameResolutionError(
            f"Ambiguous name '{name}': {len(exact)} parts share it "
            f"(indices {[it.index for it in exact]}). Refusing to guess; use the "
            f"index or a unique instance name."
        )
    # No exact match -> try case-insensitive, still refusing if ambiguous.
    ci = [it for it in items if it.name.lower() == name.lower()]
    if len(ci) == 1:
        return ci[0]
    if len(ci) > 1:
        raise NameResolutionError(
            f"Ambiguous name '{name}' (case-insensitive match hit {len(ci)} parts)."
        )
    if index_fallback is not None:
        for it in items:
            if it.index == index_fallback:
                return it
    available = ", ".join(sorted(it.name for it in items)) or "(none)"
    raise NameResolutionError(
        f"No part named '{name}'. Available: {available}."
    )
