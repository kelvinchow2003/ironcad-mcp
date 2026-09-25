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
import json
import os
import shutil
import time
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


class GeometryError(RuntimeError):
    """Raised when an operation needs geometry/a feature that isn't there
    (e.g. no extrude feature to resize, no bounding box available) — a
    real modeling-shape mismatch, not a name/mode/connectivity problem."""


class ComUnavailableError(RuntimeError):
    """Raised when the COM worker can't service a call (not running, or the
    call didn't return within IRONCAD_MCP_COM_TIMEOUT_S — see com_worker.py).
    Distinct from a normal tool-level failure: this means the CONNECTION to
    IronCAD itself is the problem, not the specific operation."""


class UnsupportedCatalogPart(RuntimeError):
    """Raised when a catalog entry doesn't support the operation requested
    of it (e.g. asking ironcad_add_catalog_assembly to treat a plain leaf
    part as a compound assembly)."""


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


def _retention_count() -> int:
    """Max backups to KEEP per source file (spec §7.2). Default 20; set
    IRONCAD_MCP_BACKUP_RETENTION_COUNT=0 (or negative) to disable pruning."""
    raw = os.environ.get("IRONCAD_MCP_BACKUP_RETENTION_COUNT", "20")
    try:
        return int(raw)
    except ValueError:
        return 20


def prune_old_backups(backup_dir_path: str, safe_name: str, keep: Optional[int] = None) -> list[str]:
    """Delete the OLDEST backups for one source file beyond `keep` (default:
    IRONCAD_MCP_BACKUP_RETENTION_COUNT). Never touches other source files'
    backups (matched by the `<safe_name>_backup_*.ics` filename prefix this
    module writes). Returns the paths removed. Never raises — pruning failure
    must not block or fail the write that triggered it."""
    if keep is None:
        keep = _retention_count()
    if keep <= 0:
        return []
    try:
        candidates = [
            f for f in os.listdir(backup_dir_path)
            if f.startswith(f"{safe_name}_backup_") and f.endswith(".ics")
        ]
    except Exception:  # noqa: BLE001
        return []
    if len(candidates) <= keep:
        return []
    # Filename embeds a sortable timestamp (_backup_YYYYMMDD_HHMMSS.ics), so a
    # plain lexicographic sort is also chronological — oldest first.
    candidates.sort()
    to_remove = candidates[: len(candidates) - keep]
    removed = []
    for name in to_remove:
        path = os.path.join(backup_dir_path, name)
        try:
            os.remove(path)
            removed.append(path)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Could not prune old backup %s: %s", path, exc)
    if removed:
        _logger.info("Pruned %d old backup(s) for %r (keeping %d)",
                      len(removed), safe_name, keep)
    return removed


def _record_write_audit(backup_dir_path: str, entry: dict) -> None:
    """Append one JSONL line to `<backup_dir>/audit.log.jsonl` (spec §7.3).

    Best-effort: a failure here must never abort the write it's auditing —
    the backup copy itself is the real safety net; this is visibility on top."""
    try:
        entry = {"timestamp": _dt.datetime.now().isoformat(timespec="seconds"), **entry}
        path = os.path.join(backup_dir_path, "audit.log.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Could not append to write-audit log: %s", exc)


def backup_active_doc(doc_path: str | None, action: Optional[str] = None) -> Optional[str]:
    """Back up the active document before a mutation, via a plain FILE COPY.

    We deliberately do NOT use IronCAD's ``SaveAsCopy`` for backups: it pops
    modal dialogs (e.g. on scenes with linked catalog parts, or bad enum args)
    that irrecoverably wedge the STA COM worker thread (see API_NOTES §8).

    Semantics:
      * If ``doc_path`` is a real on-disk ``.ics`` file (a document the user has
        saved), copy it to ``IRONCAD_MCP_BACKUP_DIR`` with a timestamped name and
        return the backup path. This snapshots the last-saved version before the
        first Claude mutation (Claude never auto-saves). If the copy fails, abort.
        After a successful copy, prunes old backups for the SAME source file
        beyond the retention count (§7.2), and appends a write-audit line
        (§7.3, `audit.log.jsonl` in the backup dir) recording what was backed
        up, where, and (if the caller passed it) which tool/`action` triggered
        the write — so a session's mutations can be reconstructed later.
      * If ``doc_path`` is not an existing file (a never-saved / new scratch
        scene), there is nothing on disk to lose — return ``None`` and allow the
        write (still recorded in the audit log with `backup_path: null`, if a
        backup dir is configured, so unsaved-scene writes are visible too).

    `action` is an optional label (e.g. the calling tool's name) for the audit
    entry only — it does not affect backup/write behavior.

    Returns the backup path, or ``None`` when the document is unsaved.
    """
    if not doc_path or not os.path.isfile(doc_path):
        _logger.info("No on-disk file for active document (%r); skipping backup "
                     "(nothing to lose).", doc_path)
        d = backup_dir()
        if d and os.path.isdir(d):
            _record_write_audit(d, {"source": doc_path, "backup_path": None, "action": action})
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
    # Two writes in the same second would otherwise share a backup name and
    # the second copy would silently replace the first snapshot (found live
    # 2026-09-24: a pre-save snapshot was lost that way). Suffix a counter.
    stem = f"{safe_name}_backup_{_timestamp()}"
    dst = os.path.join(d, f"{stem}.ics")
    n = 1
    while os.path.exists(dst):
        dst = os.path.join(d, f"{stem}_{n:02d}.ics")
        n += 1
    # IronCAD briefly holds the file open right after IZDoc.Save(); a copy
    # started in that window fails with WinError 32 (sharing violation).
    last_exc: Optional[Exception] = None
    for attempt in range(5):
        try:
            shutil.copy2(doc_path, dst)
            last_exc = None
            break
        except PermissionError as exc:
            last_exc = exc
            time.sleep(0.2 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            break
    if last_exc is not None:
        raise WriteRefused(f"Aborting write: backup copy failed ({last_exc}).") from last_exc
    _logger.info("Backup copied: %s -> %s", doc_path, dst)
    prune_old_backups(d, safe_name)
    _record_write_audit(d, {"source": doc_path, "backup_path": dst, "action": action})
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

    ``index_fallback`` is consulted whenever plain name resolution does not
    produce a single unambiguous hit — i.e. on an AMBIGUOUS name (2+ parts
    share it, the common case for symmetric stock catalog parts whose
    auto-BOM name collapses on identical geometry) as well as on a MISSING
    name. It is only ignored when the name alone already resolves uniquely,
    so an unambiguous name always takes priority over a stale/wrong index.
    (Fixed 2026-09-24: previously the index was only ever tried after a
    *zero*-match search, so passing ``index`` never helped disambiguate an
    ambiguous name — the exact case that matters most in practice.)
    """
    items = list(items)

    def _by_index() -> Optional[NamedItem]:
        if index_fallback is None:
            return None
        for it in items:
            if it.index == index_fallback:
                return it
        return None

    exact = [it for it in items if it.name == name]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        hit = _by_index()
        if hit is not None:
            return hit
        raise NameResolutionError(
            f"Ambiguous name '{name}': {len(exact)} parts share it "
            f"(indices {[it.index for it in exact]}). Refusing to guess; pass "
            f"the `index` of the one you mean, or a unique instance name."
        )
    # No exact match -> try case-insensitive, still consulting index on ambiguity.
    ci = [it for it in items if it.name.lower() == name.lower()]
    if len(ci) == 1:
        return ci[0]
    if len(ci) > 1:
        hit = _by_index()
        if hit is not None:
            return hit
        raise NameResolutionError(
            f"Ambiguous name '{name}' (case-insensitive match hit {len(ci)} "
            f"parts, indices {[it.index for it in ci]}). Pass the `index` of "
            f"the one you mean."
        )
    hit = _by_index()
    if hit is not None:
        return hit
    available = ", ".join(sorted(it.name for it in items)) or "(none)"
    raise NameResolutionError(
        f"No part named '{name}'. Available: {available}."
    )
