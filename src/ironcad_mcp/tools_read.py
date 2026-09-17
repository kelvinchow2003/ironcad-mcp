"""Tier B — read / introspection tools (spec §6, M2). All verified via API_NOTES.

Registered onto the shared FastMCP instance by ``register(mcp)``. Every COM
interaction is routed through ``run_on_com``; failures become clean error dicts.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Optional

from mcp.server.fastmcp import Image

from .com_worker import run_on_com
from .connection import get_state
from .logging_setup import get_logger
from .safety import NamedItem, resolve_by_name

_logger = get_logger()

# IZElement.Type observed values (API_NOTES §4).
_TYPE_NAMES = {1: "part", 2: "assembly"}

# ExportImage eFormat (API_NOTES §2): JPEG is the one Claude can read.
_EXPORT_JPEG = 3


def _type_name(t: Any) -> str:
    try:
        return _TYPE_NAMES.get(int(t), f"type_{int(t)}")
    except Exception:  # noqa: BLE001
        return "unknown"


def _element_summary(state, element: Any, index: int) -> dict:
    """Read a shallow summary of an IZElement (on the COM thread)."""
    name = None
    etype = None
    eid = None
    for attr, dst in (("Name", "name"), ("Type", "type"), ("Id", "id")):
        try:
            val = getattr(element, attr)
            if attr == "Name":
                name = str(val)
            elif attr == "Type":
                etype = val
            else:
                eid = val
        except Exception:  # noqa: BLE001
            pass
    return {
        "index": index,
        "name": name,
        "type": _type_name(etype),
        "type_code": (int(etype) if etype is not None else None),
        "id": (int(eid) if eid is not None else None),
    }


def _position_xyz(state, element: Any) -> Optional[list]:
    try:
        se = state.scene_element(element)
        return [float(se.PositionTransformComponentValue[t]) for t in range(3)]
    except Exception:  # noqa: BLE001
        return None


def _parameter_names(state, element: Any) -> list:
    names = []
    try:
        se = state.scene_element(element)
        pmgr = se.ParameterMgr
        cnt = int(pmgr.Count)
        for i in range(min(cnt, 200)):
            try:
                p = pmgr.GetParameter(i)
                names.append(str(getattr(p, "Name", f"param_{i}")))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return names


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def ironcad_get_active_doc_info() -> dict:
        """Return metadata about the open IronCAD document.

        Returns: {name (full path), top_level_count, connected}. Use to confirm
        which file is open before reading or building. Never mutates.
        """
        state = get_state()

        def work():
            name = state.active_doc_name()
            count = None
            try:
                count = int(state.scene().GetChildrenElementsCount())
            except Exception:  # noqa: BLE001
                count = None
            return {"name": name, "top_level_count": count, "connected": state.connected}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "connected": False}

    @mcp.tool()
    async def ironcad_list_parts() -> dict:
        """List the top-level elements (parts/assemblies) of the active scene.

        Returns: {count, parts: [{index, name, type, type_code, id}]}. Names are
        the primary identifier for describe/move/edit tools. Read-only.
        """
        state = get_state()

        def work():
            parts = []
            for i, el in enumerate(state.iter_top_elements()):
                parts.append(_element_summary(state, el, i))
            return {"count": len(parts), "parts": parts}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    @mcp.tool()
    async def ironcad_describe_part(name: str, index: Optional[int] = None) -> dict:
        """Describe one part by NAME (index as fallback).

        Returns type, id, position [x,y,z] (metres), and editable parameter names.
        Ambiguous or missing names are refused with the list of available names —
        it never guesses. Read-only.
        """
        state = get_state()

        def work():
            items = []
            elements = []
            for i, el in enumerate(state.iter_top_elements()):
                nm = None
                try:
                    nm = str(el.Name)
                except Exception:  # noqa: BLE001
                    nm = f"<element {i}>"
                items.append(NamedItem(i, nm, el))
                elements.append(el)
            chosen = resolve_by_name(items, name, index_fallback=index)
            el = chosen.obj
            summary = _element_summary(state, el, chosen.index)
            summary["position_xyz_m"] = _position_xyz(state, el)
            summary["parameters"] = _parameter_names(state, el)
            return summary

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "name": name}

    @mcp.tool()
    async def ironcad_get_selection() -> dict:
        """Return the parts the user currently has selected in IronCAD.

        Returns {count, parts:[...]} or a note if the selection API shape is not
        yet mapped. Read-only.
        """
        state = get_state()

        def work():
            scene = state.scene()
            selmgr = scene.SelectionMgr
            # Selection enumeration members are not yet verified; probe defensively.
            for count_attr in ("Count", "SelectionCount", "NumSelected"):
                try:
                    n = int(getattr(selmgr, count_attr))
                    break
                except Exception:  # noqa: BLE001
                    n = None
            result = {"count": n, "note": None}
            if n is None:
                result["note"] = ("Selection manager present but its enumeration "
                                  "members are not yet mapped (API_NOTES §5 open).")
            return result

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    @mcp.tool()
    async def ironcad_capture_view(path: Optional[str] = None, width: int = 1200,
                                   height: int = 900, fit_scene: bool = True) -> Any:
        """Export the current IronCAD viewport to a JPEG and return it as an image.

        This enables visual verification of a build. Optionally also saves to
        `path` (a .jpg file). Returns the image to Claude. Read-only (writes only
        an image file, never the model).
        """
        state = get_state()
        out_path = path or os.path.join(tempfile.gettempdir(), "ironcad_capture_view.jpg")

        def work():
            scene = state.scene()
            try:
                scene.AdjustCameraToFitShapeInRect(int(width), int(height))
            except Exception:  # noqa: BLE001
                pass  # non-fatal; export still works
            scene.ExportImage(out_path, _EXPORT_JPEG, int(width), int(height), bool(fit_scene))
            if not os.path.exists(out_path):
                raise RuntimeError("ExportImage returned but no file was written.")
            return out_path

        try:
            written = await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        # FastMCP Image reads the file and returns it as image content.
        return Image(path=written)
