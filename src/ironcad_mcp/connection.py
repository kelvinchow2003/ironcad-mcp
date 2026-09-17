"""IronCAD connection state (spec Section 4, Tier A) — VERIFIED against ICAPI.

All methods here assume they run **on the COM worker thread** (they create/touch
STA COM objects). Call them via ``run_on_com(...)`` from tool handlers.

The verified attach path (see API_NOTES.md §0) bypasses ``python_ironcad``'s
broken ``GetIZBaseApp()`` and Queries the interface ourselves:

    app  = comtypes.client.GetActiveObject("IronCAD.Application")
    base = app.QueryInterface(ICAPI.IZBaseApp)

Failure modes surfaced as clean strings:
  1. IronCAD not running        -> GetActiveObject raises.
  2. API COM DLLs not registered -> QueryInterface(IZBaseApp) raises (looks like
     the old ZIronCADApp error) -> point at elevated ``python -m python_ironcad``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from .logging_setup import get_logger

_logger = get_logger()

REGISTER_HINT = (
    "IronCAD's API COM components are not registered on this machine (could not "
    "obtain the IZBaseApp interface). Run this ONCE from an elevated "
    "(Administrator) terminal, then restart the MCP server:\n"
    '    "<venv>\\Scripts\\python.exe" -m python_ironcad\n'
    "(UAC prompt; it regsvr32-registers the ICAPI DLLs. See README.md.)"
)

NOT_RUNNING_HINT = (
    "Could not attach to a running IronCAD. Open IronCAD 2024 first (this server "
    "never launches it), then call ironcad_attach again."
)


def _looks_like_registration_error(exc: BaseException) -> bool:
    text = f"{exc}".lower()
    return (
        "zironcadapp" in text
        or "not properly registered" in text
        or "no interface" in text
        or "e_nointerface" in text
        or "0x80004002" in text
    )


@dataclass
class ConnectionState:
    """Mutable connection state, owned by the COM thread."""

    app: Any = None                    # POINTER(IUnknown) from GetActiveObject
    base: Any = None                   # IZBaseApp
    connected: bool = False
    api_registered: Optional[bool] = None
    last_error: Optional[str] = None
    ironcad_version: str = "2024 (v26.0)"
    _pi: Any = field(default=None, repr=False)      # python_ironcad module
    _icapi: Any = field(default=None, repr=False)   # comtypes.gen.ICAPIIRONCADLib

    # ---- imports (noisy; worker wraps stdout) --------------------------

    def _ensure_imports(self) -> None:
        if self._icapi is not None:
            return
        import python_ironcad as pi           # prints banner -> redirected by worker
        import comtypes.gen.ICAPIIRONCADLib as ICAPI
        self._pi = pi
        self._icapi = ICAPI

    @property
    def ICAPI(self) -> Any:
        self._ensure_imports()
        return self._icapi

    # ---- attach --------------------------------------------------------

    def attach(self) -> dict:
        """Attach to running IronCAD and resolve IZBaseApp (verified QI path)."""
        self.last_error = None
        try:
            self._ensure_imports()
        except Exception as exc:  # noqa: BLE001
            self.connected = False
            self.last_error = f"Failed to import ICAPI type library: {exc!r}"
            _logger.exception("ICAPI import failed")
            return self.status()

        import comtypes.client

        try:
            self.app = comtypes.client.GetActiveObject("IronCAD.Application")
        except Exception as exc:  # noqa: BLE001
            self.connected = False
            self.base = None
            self.last_error = NOT_RUNNING_HINT + f"  (underlying: {exc})"
            _logger.warning("GetActiveObject failed: %r", exc)
            return self.status()

        try:
            self.base = self.app.QueryInterface(self._icapi.IZBaseApp)
            self.api_registered = True
            self.connected = True
            _logger.info("Attached; IZBaseApp resolved via QueryInterface")
        except Exception as exc:  # noqa: BLE001
            self.base = None
            self.connected = False
            if _looks_like_registration_error(exc):
                self.api_registered = False
                self.last_error = REGISTER_HINT
                _logger.error("IZBaseApp QI failed: API not registered")
            else:
                self.last_error = f"QueryInterface(IZBaseApp) failed: {exc}"
                _logger.exception("Unexpected IZBaseApp QI failure")
        return self.status()

    # ---- helpers (run on COM thread) -----------------------------------

    def require_base(self) -> Any:
        if self.base is None:
            self.attach()
        if self.base is None:
            raise RuntimeError(self.last_error or "Not connected to IronCAD.")
        return self.base

    def active_doc(self) -> Any:
        base = self.require_base()
        try:
            return base.ActiveDoc
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Could not read ActiveDoc: {exc}") from exc

    def scene(self) -> Any:
        """ActiveDoc QI'd to IZSceneDoc (the rich scene interface). Raises if none."""
        doc = self.active_doc()
        if doc is None:
            raise RuntimeError("No active document is open in IronCAD.")
        try:
            return doc.QueryInterface(self._icapi.IZSceneDoc)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Active doc is not a scene document: {exc}") from exc

    def catalog_mgr(self) -> Any:
        return self.require_base().CatalogMgr

    def active_doc_name(self) -> Optional[str]:
        try:
            doc = self.active_doc()
        except Exception:  # noqa: BLE001
            return None
        if doc is None:
            return None
        for attr in ("Name", "FileName", "Title"):
            try:
                val = getattr(doc, attr)
                if val:
                    return str(val)
            except Exception:  # noqa: BLE001
                continue
        return "<unnamed document>"

    def iter_top_elements(self) -> Iterator[Any]:
        """Yield top-level IZElement children of the active scene (COM thread)."""
        scene = self.scene()
        try:
            child = scene.GetFirstChild()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Could not enumerate scene: {exc}") from exc
        guard = 0
        while child is not None and guard < 100_000:
            yield child
            guard += 1
            try:
                child = scene.GetNextChild()
            except Exception:  # noqa: BLE001
                break

    def scene_element(self, element: Any) -> Any:
        """QI an IZElement to IZSceneElement (transform/params)."""
        return element.QueryInterface(self._icapi.IZSceneElement)

    # ---- status --------------------------------------------------------

    def status(self) -> dict:
        active_name: Optional[str] = None
        catalogs_loaded: Optional[int] = None
        if self.connected:
            try:
                active_name = self.active_doc_name()
            except Exception:  # noqa: BLE001
                active_name = None
            try:
                catalogs_loaded = int(self.catalog_mgr().Count)
            except Exception:  # noqa: BLE001
                catalogs_loaded = None
        return {
            "connected": bool(self.connected),
            "api_registered": self.api_registered,
            "active_doc_name": active_name,
            "ironcad_version": self.ironcad_version if self.connected else None,
            "catalogs_loaded": catalogs_loaded,
            "last_error": self.last_error,
        }


_state: Optional[ConnectionState] = None


def get_state() -> ConnectionState:
    global _state
    if _state is None:
        _state = ConnectionState()
    return _state
