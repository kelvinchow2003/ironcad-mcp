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

# eZAnchorBehavior names (API_NOTES §9b).
_ANCHOR_BEHAVIOR_NAMES = {
    0: "move_freely", 1: "fixed_position",
    2: "attached_to_surface", 3: "slide_along_surface",
}


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


def _anchor_info(state, element: Any) -> Optional[dict]:
    """Read a part's anchor: local [x,y,z] (metres) + behavior (API_NOTES §9b)."""
    try:
        se = state.scene_element(element)
        xyz = [float(se.AnchorTransformComponentValue[t]) for t in range(3)]
        code = None
        try:
            code = int(se.AnchorBehavior)
        except Exception:  # noqa: BLE001
            pass
        return {"anchor_xyz_m": xyz, "behavior": code,
                "behavior_name": _ANCHOR_BEHAVIOR_NAMES.get(code)}
    except Exception:  # noqa: BLE001
        return None


def _bbox_list(part) -> Optional[list]:
    """Return [minx,miny,minz,maxx,maxy,maxz] from IZPart.GetBoundingBox (global)."""
    try:
        v = part.GetBoundingBox(False)  # False = global space
        return [float(x) for x in v]
    except Exception:  # noqa: BLE001
        return None


def _element_bbox(state, element: Any) -> Optional[list]:
    """Global AABB for an element, via QI IZPart. None if not a part/no bbox."""
    try:
        part = element.QueryInterface(state.ICAPI.IZPart)
    except Exception:  # noqa: BLE001
        return None
    return _bbox_list(part)


def _iter_leaf_parts(state, element: Any, depth: int = 0):
    """Yield (name, element, bbox) for every leaf PART under `element` (recursing
    into assemblies). Assemblies themselves don't QI to IZPart, so we recurse."""
    bb = _element_bbox(state, element)
    if bb is not None:
        try:
            nm = str(element.Name)
        except Exception:  # noqa: BLE001
            nm = "<part>"
        yield (nm, element, bb)
        return
    if depth > 12:
        return
    kids = None
    try:
        kids = element.GetChildren()
    except Exception:  # noqa: BLE001
        kids = None
    if not kids:
        return
    try:
        iterator = list(kids)
    except TypeError:
        return
    for k in iterator:
        yield from _iter_leaf_parts(state, k, depth + 1)


def _aabb_overlap(a: list, b: list, tol: float):
    """Overlap box dims of two AABBs [minx..maxz]; None if they don't overlap
    by more than `tol` on every axis. Returns [ox,oy,oz] overlap extents."""
    ov = []
    for k in range(3):
        lo = max(a[k], b[k])
        hi = min(a[k + 3], b[k + 3])
        d = hi - lo
        if d <= tol:
            return None
        ov.append(round(d, 6))
    return ov


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
            summary["anchor"] = _anchor_info(state, el)
            summary["parameters"] = _parameter_names(state, el)
            return summary

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "name": name}

    @mcp.tool()
    async def ironcad_get_anchor(name: str, index: Optional[int] = None) -> dict:
        """Read a part's ANCHOR (the blue/white sphere), by NAME (index fallback).

        Returns {name, anchor_xyz_m (local), behavior, behavior_name}. The anchor
        is the point IronCAD places/snaps the part by; behavior is one of
        move_freely(0)/fixed_position(1)/attached_to_surface(2)/slide_along_surface(3).
        Read-only. Use with ironcad_set_anchor to control snapping.
        """
        state = get_state()

        def work():
            items = []
            for i, el in enumerate(state.iter_top_elements()):
                try:
                    nm = str(el.Name)
                except Exception:  # noqa: BLE001
                    nm = f"<element {i}>"
                items.append(NamedItem(i, nm, el))
            chosen = resolve_by_name(items, name, index_fallback=index)
            info = _anchor_info(state, chosen.obj) or {}
            return {"name": chosen.name, **info}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "name": name}

    @mcp.tool()
    async def ironcad_get_part_bbox(name: str, index: Optional[int] = None) -> dict:
        """Read a part's bounding box (dimensions + extents), by NAME.

        Use this to MEASURE geometry and place parts by CALCULATION instead of
        screenshots: e.g. read a profile's cross-section (a 40x40 extrusion reports
        dims ~[0.04,0.04,L]) and its min/max corners, then compute exact offsets so
        parts butt instead of interpenetrating.

        Returns {name, dims_m:[dx,dy,dz], min_m:[x,y,z], max_m:[x,y,z],
        center_m:[x,y,z]} in GLOBAL metres. Read-only.
        """
        state = get_state()

        def work():
            items = []
            for i, el in enumerate(state.iter_top_elements()):
                try:
                    nm = str(el.Name)
                except Exception:  # noqa: BLE001
                    nm = f"<element {i}>"
                items.append(NamedItem(i, nm, el))
            chosen = resolve_by_name(items, name, index_fallback=index)
            bb = _element_bbox(state, chosen.obj)
            if bb is None:
                raise RuntimeError(
                    f"No bounding box for '{chosen.name}' (not a part, or geometry "
                    f"not available)."
                )
            dims = [round(bb[k + 3] - bb[k], 6) for k in range(3)]
            center = [round((bb[k] + bb[k + 3]) / 2.0, 6) for k in range(3)]
            return {"name": chosen.name,
                    "dims_m": dims,
                    "min_m": [round(bb[k], 6) for k in range(3)],
                    "max_m": [round(bb[k + 3], 6) for k in range(3)],
                    "center_m": center}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "name": name}

    @mcp.tool()
    async def ironcad_check_interference(tolerance_mm: float = 0.5) -> dict:
        """Report parts whose solid bodies OVERLAP (interpenetrate), by CALCULATION.

        Computes pairwise axis-aligned bounding-box overlaps for every part in the
        scene (recursing into assemblies) — no screenshot needed. This is how the
        build loop should validate joints (e.g. that framed extrusions butt rather
        than run into each other) instead of relying on captured views.

        `tolerance_mm` ignores overlaps thinner than this on any axis (touching
        faces are not clashes). Returns {part_count, pairs_checked, clash_count,
        clashes:[{a, b, overlap_mm:[x,y,z]}]}, worst first.

        NOTE: AABB overlap is a conservative proxy — two non-box parts whose boxes
        overlap may not truly collide — but for axis-aligned extrusion frames it is
        exact enough to catch corner interpenetration.
        """
        state = get_state()
        tol = float(tolerance_mm) / 1000.0

        def work():
            parts = []
            for el in state.iter_top_elements():
                parts.extend(_iter_leaf_parts(state, el))
            clashes = []
            n = len(parts)
            checked = 0
            for i in range(n):
                for j in range(i + 1, n):
                    checked += 1
                    ov = _aabb_overlap(parts[i][2], parts[j][2], tol)
                    if ov is not None:
                        clashes.append({
                            "a": parts[i][0], "b": parts[j][0],
                            "overlap_mm": [round(x * 1000.0, 3) for x in ov],
                            "_vol": ov[0] * ov[1] * ov[2],
                        })
            clashes.sort(key=lambda c: c["_vol"], reverse=True)
            for c in clashes:
                c.pop("_vol", None)
            return {"part_count": n, "pairs_checked": checked,
                    "clash_count": len(clashes), "clashes": clashes}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

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
