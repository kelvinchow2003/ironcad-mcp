"""Tier E — escape hatch (spec §6, M4, gated).

`ironcad_execute_api_script` runs a Python snippet ON the COM worker thread with
live ICAPI refs in scope. It is a pragmatic power tool given the sparse docs, but
it is **arbitrary code against the live CAD session and is NOT a sandbox**. It
defaults to read-only intent; mutating requires allow_writes=True, which also
takes a backup first (spec §7).

In-scope names for the snippet:
    ICAPI      - comtypes.gen.ICAPIIRONCADLib module
    app        - the IronCAD.Application COM object (IUnknown)
    base       - IZBaseApp
    doc        - ActiveDoc (IZDoc) or None
    scene      - ActiveDoc QI'd to IZSceneDoc, or None
    catalog_mgr- IZCatalogMgr
    state      - the ConnectionState (helpers: scene_element(), iter_top_elements())

Set a variable named `result` (or use the last expression) to return a value.
"""

from __future__ import annotations

import io
import traceback
from contextlib import redirect_stdout
from typing import Any

from .com_worker import run_on_com
from .connection import get_state
from .logging_setup import get_logger
from .safety import backup_active_doc, require_write_mode

_logger = get_logger()


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def ironcad_execute_api_script(code: str, allow_writes: bool = False) -> dict:
        """Run a Python snippet against the live IronCAD COM session (ESCAPE HATCH).

        NOT a sandbox. Defaults to read-only intent; set allow_writes=True to
        permit mutation (also requires read_write mode and takes a backup first).
        In scope: ICAPI, app, base, doc, scene, catalog_mgr, state. Assign
        `result` to return a value. Returns {result, stdout, ok} or {error, traceback}.
        """
        state = get_state()

        def work():
            if allow_writes:
                require_write_mode("ironcad_execute_api_script(allow_writes=True)")
                backup_active_doc(state.active_doc_name())
            state.require_base()
            doc = None
            scene = None
            try:
                doc = state.active_doc()
            except Exception:  # noqa: BLE001
                doc = None
            if doc is not None:
                try:
                    scene = doc.QueryInterface(state.ICAPI.IZSceneDoc)
                except Exception:  # noqa: BLE001
                    scene = None
            env: dict[str, Any] = {
                "ICAPI": state.ICAPI,
                "app": state.app,
                "base": state.base,
                "doc": doc,
                "scene": scene,
                "catalog_mgr": state.catalog_mgr(),
                "state": state,
            }
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    # Try eval (expression) first for a natural return value.
                    try:
                        result = eval(compile(code, "<ironcad_script>", "eval"), env)  # noqa: S307
                    except SyntaxError:
                        exec(compile(code, "<ironcad_script>", "exec"), env)  # noqa: S102
                        result = env.get("result")
                return {"ok": True, "result": _stringify(result), "stdout": buf.getvalue()}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc),
                        "traceback": traceback.format_exc(), "stdout": buf.getvalue()}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001 - e.g. WriteRefused / not connected
            return {"ok": False, "error": str(exc)}


def _stringify(value: Any) -> Any:
    """Make a COM/arbitrary result JSON-safe."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_stringify(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _stringify(v) for k, v in value.items()}
    try:
        return repr(value)
    except Exception:  # noqa: BLE001
        return "<unrepresentable>"
