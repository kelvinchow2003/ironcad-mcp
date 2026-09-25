"""Tier B — read / introspection tools (spec §6, M2). All verified via API_NOTES.

Registered onto the shared FastMCP instance by ``register(mcp)``. Every COM
interaction is routed through ``run_on_com``; failures become clean error dicts.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Optional

from mcp.server.fastmcp import Image

from .catalog_manifest import load_manifest
from .com_worker import run_on_com
from .connection import get_state
from .logging_setup import get_logger
from .safety import NamedItem, resolve_by_name

_logger = get_logger()

# IZElement.Type observed values (API_NOTES §4).
_TYPE_NAMES = {1: "part", 2: "assembly"}

# ExportImage eFormat (API_NOTES §2): JPEG is the one Claude can read.
_EXPORT_JPEG = 3

# capture_view camera presets: eye direction FROM the scene centre (Z up).
# A fresh scene's default camera can sit below the floor looking up (seen
# live 2026-09-24), which makes the verification render unreadable.
_VIEW_PRESETS = {
    "iso": (1.0, -1.3, 0.8),
    "iso_rear": (-1.0, 1.4, 0.7),
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "right": (1.0, 0.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "top": (0.0, -1e-3, 1.0),
}


def _set_camera_preset(state, scene, view: str) -> None:
    """Aim the active camera at the scene's bbox centre from a preset
    direction. IZCamera's Position/Direction/Up setters take a VARIANT that
    must be a SAFEARRAY of doubles — a plain tuple marshals as a VARIANT
    array and is rejected with E_INVALIDARG (verified live)."""
    import array
    import math

    lo, hi = [1e18] * 3, [-1e18] * 3
    for el in state.iter_top_elements():
        bb = _element_bbox(state, el)
        if bb:
            lo = [min(lo[k], bb[k]) for k in range(3)]
            hi = [max(hi[k], bb[k + 3]) for k in range(3)]
    if lo[0] > hi[0]:
        lo, hi = [0.0] * 3, [0.0] * 3
    c = [(lo[k] + hi[k]) / 2.0 for k in range(3)]
    span = max(max(hi[k] - lo[k] for k in range(3)), 0.1)
    e = _VIEW_PRESETS[view]
    n = math.sqrt(sum(v * v for v in e))
    eye = [c[k] + e[k] / n * span * 3.0 for k in range(3)]
    d = [(c[k] - eye[k]) for k in range(3)]
    dn = math.sqrt(sum(v * v for v in d))
    d = [v / dn for v in d]
    up = [0.0, 0.0, 1.0] if view != "top" else [0.0, 1.0, 0.0]
    dot = sum(up[k] * d[k] for k in range(3))
    up = [up[k] - dot * d[k] for k in range(3)]
    un = math.sqrt(sum(v * v for v in up))
    up = [v / un for v in up]
    cam = scene.CameraMgr.ActiveCamera
    dbl = lambda v: array.array("d", [float(x) for x in v])  # noqa: E731
    cam.Position = dbl(eye)
    cam.CenterOfInterest = dbl(c)
    cam.Direction = dbl(d)
    cam.Up = dbl(up)
    cam.Direction = dbl(d)  # re-apply: Up setter can re-orthogonalise it


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
    """Global AABB for an element. Tries IZPart first (leaf parts), then
    IZAssembly (fixed 2026-09-24: IZAssembly.GetBoundingBox works directly on
    a compound/grouped element without descending into children -- verified
    live for both a catalog compound assembly, e.g. PAN___P_N___+PIN4521, and
    a user AssembleElements group). None if neither QI succeeds."""
    try:
        part = element.QueryInterface(state.ICAPI.IZPart)
        return _bbox_list(part)
    except Exception:  # noqa: BLE001
        pass
    try:
        asm = element.QueryInterface(state.ICAPI.IZAssembly)
    except Exception:  # noqa: BLE001
        return None
    return _bbox_list(asm)


def _child_elements(state, element: Any) -> list:
    """Direct children of an assembly-typed element, as real IZElements.

    Fixed 2026-09-24 (PRODUCTION_READINESS_PLAN Phase 2.4): `IZElement.
    GetChildren()` returns unusable values on this IronCAD build -- each
    "child" comtypes hands back is a bare Python `int` (confirmed live: a raw
    memory-address-shaped value, not an element id `GetElementById` can
    resolve either -- GetChildren() cannot be salvaged by post-processing).
    The verified working path is `GetChildrenZArray()` (an IZArray), whose
    `.Get(i)` items come back as `POINTER(IUnknown)` that DO successfully
    QueryInterface to IZElement -- confirmed live against the user's real
    `b150004_173.ics` (`Assembly1` -> 3 PIL4140SNN_ members + 2 FAS4041 +
    4 CAP4041, all correctly named/typed/bboxed via this path).
    """
    try:
        za = element.GetChildrenZArray()
        count = int(za.Count())
    except Exception:  # noqa: BLE001
        return []
    out = []
    for i in range(count):
        try:
            raw = za.Get(i)
            kid = raw.QueryInterface(state.ICAPI.IZElement)
        except Exception:  # noqa: BLE001
            continue
        out.append(kid)
    return out


def _iter_leaf_parts(state, element: Any, depth: int = 0):
    """Yield (name, element, bbox) for every leaf PART under `element`,
    recursing into assemblies via `_child_elements` (see its docstring for
    why the older GetChildren()-based recursion was silently broken).

    Dispatches on the element's real Type (1=part, 2=assembly) rather than on
    "does a bbox lookup succeed" -- since the `_element_bbox` fix above now
    ALSO succeeds for assemblies (via IZAssembly), so that could no longer be
    used to distinguish "leaf" from "recurse into me". Interference checking
    wants the fine-grained leaf parts, not one coarse box per assembly.
    """
    if depth > 12:
        return
    etype = None
    try:
        etype = int(element.Type)
    except Exception:  # noqa: BLE001
        pass
    if etype == 2:
        kids = _child_elements(state, element)
        if kids:
            for k in kids:
                yield from _iter_leaf_parts(state, k, depth + 1)
            return
        # No enumerable children (empty group, or enumeration failed) --
        # fall through and report the assembly's own bbox rather than
        # silently dropping real geometry from the interference check.
    bb = _element_bbox(state, element)
    if bb is not None:
        try:
            nm = str(element.Name)
        except Exception:  # noqa: BLE001
            nm = "<part>"
        yield (nm, element, bb)


def _child_summary(state, element: Any, index_in_parent: int, depth: int) -> dict:
    summary = _element_summary(state, element, index_in_parent)
    summary["depth"] = depth
    bb = _element_bbox(state, element)
    if bb is not None:
        summary["bbox_m"] = {
            "dims_m": [round(bb[k + 3] - bb[k], 6) for k in range(3)],
            "min_m": [round(bb[k], 6) for k in range(3)],
            "max_m": [round(bb[k + 3], 6) for k in range(3)],
            "center_m": [round((bb[k] + bb[k + 3]) / 2.0, 6) for k in range(3)],
        }
    else:
        summary["bbox_m"] = None
    return summary


def _collect_children(state, element: Any, recursive: bool, depth: int = 1,
                       limit: int = 500) -> list:
    out: list = []
    for i, kid in enumerate(_child_elements(state, element)):
        if len(out) >= limit:
            break
        info = _child_summary(state, kid, i, depth)
        out.append(info)
        if recursive and info.get("type_code") == 2:
            out.extend(_collect_children(state, kid, recursive, depth + 1,
                                          limit - len(out)))
    return out


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
    async def ironcad_get_catalog_manifest(catalog: Optional[str] = None) -> dict:
        """Return the DECODED catalog knowledge base (spec Phase 6) — no COM
        call, works even before ironcad_attach.

        This is structured, pre-verified information about what the user's
        catalogs actually contain: real stock profile sizes, which
        parameters are genuinely settable vs. inert, which catalog entries
        fail to InsertElement() and why, which of two similarly-named
        compound assemblies uses which parameter names (e.g. the PIN4521
        panel family's Gesamtbreite/Gesamtlaenge vs. the REAL Robotunits
        CAL4515SNN+CAN4501 retention-rail family's Breite/Hoehe — easy to
        get wrong by assuming one name set applies to both), and which
        `ironcad_*` tool/technique to use for each family. Consult this
        BEFORE guessing a catalog part's behavior from its name alone, and
        before substituting a profile size a drawing calls for.

        Pass `catalog` (case-insensitive) to get one catalog's entry only;
        omit for the full manifest. Returns {catalog, found, data} for a
        single lookup, or {catalogs, general_rules, meta} for the full
        manifest. This is a SNAPSHOT (see its own `_meta` field for when it
        was last verified) — re-verify with scripts/verify_catalog_manifest.py
        if it's been a while, not an API contract.
        """
        manifest = load_manifest()
        if catalog:
            catalogs = manifest.get("catalogs", {})
            match = next((v for k, v in catalogs.items() if k.lower() == catalog.lower()), None)
            return {"catalog": catalog, "found": match is not None, "data": match}
        return {"catalogs": manifest.get("catalogs", {}),
                "general_rules": manifest.get("general_rules", []),
                "meta": manifest.get("_meta", {})}

    @mcp.tool()
    async def ironcad_validate_spec(spec_path: str) -> dict:
        """Validate a `<drawing_name>.spec.json` against the drawing-interpretation
        schema (spec Phase 5) — no COM call, works even before ironcad_attach.

        Use this after `interpret_drawing` produces a spec file and BEFORE
        `build_from_sketch` touches IronCAD: a structurally invalid or
        incomplete spec should be fixed as a JSON edit (free) rather than
        discovered mid-build (expensive). Returns {valid, errors: [...],
        spec} — `spec` is the parsed content so the caller doesn't need a
        second file read. `errors` is empty iff `valid` is true.
        """
        import json as _json

        from .spec_schema import validate_spec

        try:
            with open(spec_path, encoding="utf-8") as f:
                spec = _json.load(f)
        except Exception as exc:  # noqa: BLE001
            return {"valid": False, "errors": [f"could not read/parse {spec_path}: {exc}"],
                    "spec": None}
        errors = validate_spec(spec)
        return {"valid": not errors, "errors": errors, "spec": spec}

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
    async def ironcad_list_children(
        name: str, index: Optional[int] = None, recursive: bool = False,
    ) -> dict:
        """List the children of an ASSEMBLY part, by NAME (index fallback).

        Fixed 2026-09-24: this previously had no working implementation --
        assembly child enumeration failed silently (the underlying
        `GetChildren()` COM call returns unusable values on this IronCAD
        build). Uses the verified path instead (`GetChildrenZArray()` +
        explicit QueryInterface per child). Use this to see what a compound
        catalog assembly (e.g. `PAN___P_N___+CAL4515SNN___+CAN4501`, inserted
        via ironcad_add_catalog_assembly) actually bundles, or to inspect a
        real hand-built file's grouped sub-assemblies.

        `recursive=False` (default) returns only the immediate children.
        `recursive=True` also descends into any child that is itself an
        assembly, flattening the whole subtree (each entry's `depth` field
        says how many levels down it is).

        Refuses if `name` does not resolve to an assembly (type_code 2) --
        leaf parts have no children. Returns {name, type_code, recursive,
        count, children:[{index, name, type, type_code, id, depth,
        bbox_m:{dims_m,min_m,max_m,center_m}|null}]}. Read-only.
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
            el = chosen.obj
            etype = None
            try:
                etype = int(el.Type)
            except Exception:  # noqa: BLE001
                pass
            if etype != 2:
                raise RuntimeError(
                    f"'{chosen.name}' is not an assembly (type_code={etype}); "
                    f"it has no children to list."
                )
            children = _collect_children(state, el, bool(recursive))
            return {"name": chosen.name, "type_code": etype,
                    "recursive": bool(recursive), "count": len(children),
                    "children": children}

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
                                   height: int = 900, fit_scene: bool = True,
                                   view: Optional[str] = None) -> Any:
        """Export the current IronCAD viewport to a JPEG and return it as an image.

        This enables visual verification of a build. Optionally also saves to
        `path` (a .jpg file). `view` optionally re-aims the camera first:
        'iso' (front-right, above), 'iso_rear', 'front', 'back', 'left',
        'right' or 'top' (Z is up). Omit it to keep the user's current camera.
        Pass a view for a freshly built scene: its default camera can sit below
        the floor. Returns the image to Claude. Never changes the model (it
        changes the camera only when `view` is given, and writes only the
        image file).
        """
        state = get_state()
        out_path = path or os.path.join(tempfile.gettempdir(), "ironcad_capture_view.jpg")
        if view is not None and view not in _VIEW_PRESETS:
            return {"error": f"view must be one of {sorted(_VIEW_PRESETS)}, got {view!r}."}

        def work():
            scene = state.scene()
            if view is not None:
                _set_camera_preset(state, scene, view)
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
