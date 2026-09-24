"""Tier D — geometry edit / build tools (spec §6, M4).

All writes are gated on read_write mode and take a backup first (file-copy;
see safety.backup_active_doc). Verified ICAPI paths (API_NOTES):

* Move:  element -> QI IZSceneElement -> GetPositionTransform() (IZMathMatrix)
         -> SetTranslation(x,y,z) / SetRotation(axis,deg,applyToCurrent)
         -> SetPositionTransform(m)
* Param: IZSceneElement.ParameterMgr -> GetParameterByName(name)
         -> .Value = <float>  (or .Expression = <str>) ; scene.RegenerateParts(True)
* Save:  IZDoc.Save() in place; IZSceneDoc.SaveAs / SaveAsCopy to a path.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .com_worker import run_on_com
from .connection import get_state
from .logging_setup import get_logger
from .safety import (
    NamedItem,
    backup_active_doc,
    require_write_mode,
    resolve_by_name,
)

_logger = get_logger()

# Axis codes for IZMathMatrix.SetRotation(nAxis, dDegrees, vbApplyToCurrent).
_AXES = {"x": 0, "y": 1, "z": 2}

# eZAnchorBehavior (API_NOTES §9b, verified live). Accept ints or friendly names.
_ANCHOR_BEHAVIORS = {
    "free": 0, "move_freely": 0, "movefreely": 0,
    "fixed": 1, "fixed_position": 1, "fixedposition": 1,
    "attached": 2, "attached_to_surface": 2, "attachedtosurface": 2, "surface": 2,
    "slide": 3, "slide_along_surface": 3, "slidealongsurface": 3,
}
_ANCHOR_BEHAVIOR_NAMES = {
    0: "move_freely", 1: "fixed_position",
    2: "attached_to_surface", 3: "slide_along_surface",
}


def _coerce_behavior(behavior) -> int:
    """Map an int (0..3) or a friendly name to an eZAnchorBehavior code."""
    if isinstance(behavior, (int, float)) and not isinstance(behavior, bool):
        code = int(behavior)
        if code not in _ANCHOR_BEHAVIOR_NAMES:
            raise ValueError(f"behavior {code} out of range 0..3.")
        return code
    key = str(behavior).strip().lower().replace(" ", "_")
    if key not in _ANCHOR_BEHAVIORS:
        raise ValueError(
            f"Unknown anchor behavior {behavior!r}. Use 0..3 or one of: "
            f"move_freely, fixed_position, attached_to_surface, slide_along_surface."
        )
    return _ANCHOR_BEHAVIORS[key]


def _resolve_top_element(state, name: str, index: Optional[int]) -> Any:
    """Resolve a top-level element by name (index fallback). Runs on COM thread."""
    items = []
    for i, el in enumerate(state.iter_top_elements()):
        try:
            nm = str(el.Name)
        except Exception:  # noqa: BLE001
            nm = f"<element {i}>"
        items.append(NamedItem(i, nm, el))
    return resolve_by_name(items, name, index_fallback=index)


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def ironcad_set_part_parameter(
        name: str, param: str, value: float, index: Optional[int] = None
    ) -> dict:
        """Set a driving parameter/dimension on a part and regenerate (WRITE).

        Resolves the part by NAME (index fallback), finds `param` on its
        ParameterMgr, sets its numeric Value, and regenerates. Requires
        read_write mode; backs up first. Returns {name, param, old, new}.
        Refuses on ambiguous/missing part or missing parameter.
        """
        state = get_state()

        def work():
            require_write_mode("ironcad_set_part_parameter")
            backup_path = backup_active_doc(state.active_doc_name())
            chosen = _resolve_top_element(state, name, index)
            se = state.scene_element(chosen.obj)
            pmgr = se.ParameterMgr
            try:
                p = pmgr.GetParameterByName(param)
            except Exception as exc:  # noqa: BLE001
                # Enumerate available names for a helpful refusal.
                names = []
                try:
                    for i in range(int(pmgr.Count)):
                        names.append(str(pmgr.GetParameter(i).Name))
                except Exception:  # noqa: BLE001
                    pass
                raise RuntimeError(
                    f"No parameter '{param}' on part '{chosen.name}'. "
                    f"Available: {', '.join(names) or '(none)'}."
                ) from exc
            old = None
            try:
                old = float(p.Value)
            except Exception:  # noqa: BLE001
                pass
            p.Value = float(value)
            try:
                state.scene().RegenerateParts(True)
            except Exception:  # noqa: BLE001
                pass
            new = None
            try:
                new = float(p.Value)
            except Exception:  # noqa: BLE001
                pass
            return {"name": chosen.name, "param": param, "old": old,
                    "new": new, "backup_path": backup_path}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "name": name, "param": param}

    @mcp.tool()
    async def ironcad_set_extrude_length(
        name: str, length_m: float, index: Optional[int] = None, backward: bool = False,
    ) -> dict:
        """Set a part's REAL extrude length directly, to ANY size (WRITE).

        For stock catalog parts whose length looks "fixed" at insert (e.g.
        PIL4040SNN_ always inserts at 500mm) — that 500mm is NOT a hard limit,
        it's just the catalog's default `ExtrudeDistance`. Verified live
        2026-09-19: the part's first child feature (`element.GetFirstChild()`,
        typically named "Block") QIs to `IZExtrudeFeature`, whose
        `ExtrudeDistance[Z_EXTRUDE_DIRECTION_FORWARD]` is directly settable to
        ANY length (tested 50mm to 2500mm+) — no Boolean-cut or multi-segment
        splicing needed. The element's `.Name` auto-updates to match the new
        real length (BOM-correct: `PIL4040SNN500` -> `PIL4040SNN1076.8` etc.),
        exactly like a cut does — so this is BOM-safe, unlike renaming.

        `backward=True` grows/shrinks from the other end (Z_EXTRUDE_DIRECTION_
        BACKWARD) instead of forward — use if the part's sketch plane is at the
        far end for a given part family (check bbox after; not all families
        necessarily support both directions symmetrically, untested).

        IMPORTANT (fixed 2026-09-24 — was a real bug): the part has TWO
        independent `ExtrudeDistance` slots, one per direction. Earlier this
        tool only ever set the slot for the requested direction, leaving the
        OTHER slot at whatever the catalog default left it (typically the
        insert default, e.g. 500mm forward) — so `backward=True` silently
        produced a part `length_m + <stale other-direction length>` long, not
        `length_m`. This tool now always sets BOTH slots explicitly: the
        requested direction to `length_m`, and the other direction to 0.0, so
        the part's total length is exactly `length_m` regardless of prior
        state. (For the ordinary forward-only case this is a no-op, since the
        backward slot was already 0 by default.)

        Resolves by NAME (index fallback). Requires read_write mode; backs up
        first. Refuses if the resolved element has no `Block`-style extrude
        feature as its first child (e.g. an assembly, or a part built some
        other way) — try `ironcad_set_part_parameter` for those instead.
        Returns {name (new BOM name), old_length_m, new_length_m, dims_m,
        other_direction_length_m (should be 0.0 — confirms the fix applied)}.
        """
        state = get_state()

        def work():
            require_write_mode("ironcad_set_extrude_length")
            backup_path = backup_active_doc(state.active_doc_name())
            chosen = _resolve_top_element(state, name, index)
            el = chosen.obj
            try:
                block = el.GetFirstChild()
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"'{chosen.name}' has no child feature to edit: {exc}"
                ) from exc
            if block is None:
                raise RuntimeError(f"'{chosen.name}' has no first child feature.")
            try:
                ef = block.QueryInterface(state.ICAPI.IZExtrudeFeature)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"'{chosen.name}'.GetFirstChild() ('{getattr(block, 'Name', '?')}') "
                    f"is not an extrude feature: {exc}"
                ) from exc
            dir_code = 1 if backward else 0  # Z_EXTRUDE_DIRECTION_BACKWARD/FORWARD
            other_code = 0 if backward else 1
            old = None
            try:
                old = float(ef.ExtrudeDistance(dir_code))
            except Exception:  # noqa: BLE001
                pass
            ef.ExtrudeDistance[dir_code] = float(length_m)
            try:
                ef.ExtrudeDistance[other_code] = 0.0
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "Could not zero the other ExtrudeDistance[%d] direction on "
                    "'%s' — total length may include a stale offset: %s",
                    other_code, chosen.name, exc,
                )
            part = el.QueryInterface(state.ICAPI.IZPart)
            part.Regenerate()
            new = None
            try:
                new = float(ef.ExtrudeDistance(dir_code))
            except Exception:  # noqa: BLE001
                pass
            other_new = None
            try:
                other_new = float(ef.ExtrudeDistance(other_code))
            except Exception:  # noqa: BLE001
                pass
            dims = None
            try:
                bb = part.GetBoundingBox(False)
                dims = [round(bb[k + 3] - bb[k], 6) for k in range(3)]
            except Exception:  # noqa: BLE001
                pass
            new_name = None
            try:
                new_name = str(el.Name)
            except Exception:  # noqa: BLE001
                pass
            return {"name": new_name or chosen.name, "old_length_m": old,
                    "new_length_m": new, "other_direction_length_m": other_new,
                    "dims_m": dims, "backup_path": backup_path}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "name": name}

    @mcp.tool()
    async def ironcad_move_part(
        name: str,
        position: Optional[list] = None,
        rotation_deg: Optional[list] = None,
        index: Optional[int] = None,
        relative_to: Optional[str] = None,
        offset: Optional[list] = None,
    ) -> dict:
        """Set a part's position and/or rotation (WRITE).

        Positioning (prefer RELATIVE — it avoids guessing world coordinates):
          * `relative_to` = the NAME of another part to anchor against; this part
            moves to that part's position + `offset` ([dx,dy,dz] metres, default
            [0,0,0]). The efficient way to connect/align two parts.
          * `position` = an absolute [x,y,z] in metres, used only when
            `relative_to` is not given.

        `rotation_deg` is an optional [rx,ry,rz] applied about the X, Y, Z axes in
        degrees. Resolves by NAME (index fallback). Requires read_write mode;
        backs up first. Returns {name, position_xyz_m}.
        """
        state = get_state()

        def work():
            require_write_mode("ironcad_move_part")
            backup_path = backup_active_doc(state.active_doc_name())
            chosen = _resolve_top_element(state, name, index)
            # Compute a relative target BEFORE moving (fail fast on bad anchor).
            target_pos = None
            if relative_to:
                anchor = _resolve_top_element(state, relative_to, None)
                anchor_se = state.scene_element(anchor.obj)
                base = [float(anchor_se.PositionTransformComponentValue[t]) for t in range(3)]
                off = offset or [0.0, 0.0, 0.0]
                target_pos = [base[t] + float(off[t] if t < len(off) else 0.0)
                              for t in range(3)]
            elif position and len(position) >= 3:
                target_pos = [float(position[0]), float(position[1]), float(position[2])]
            se = state.scene_element(chosen.obj)
            m = se.GetPositionTransform()
            if rotation_deg and len(rotation_deg) >= 3:
                for axis, deg in zip(("x", "y", "z"), rotation_deg[:3]):
                    if deg:
                        m.SetRotation(_AXES[axis], float(deg), True)
            if target_pos is not None:
                m.SetTranslation(target_pos[0], target_pos[1], target_pos[2])
            se.SetPositionTransform(m)
            try:
                state.scene().Update()
            except Exception:  # noqa: BLE001
                pass
            pos = None
            try:
                pos = [float(se.PositionTransformComponentValue[t]) for t in range(3)]
            except Exception:  # noqa: BLE001
                pass
            return {"name": chosen.name, "position_xyz_m": pos, "backup_path": backup_path}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "name": name}

    @mcp.tool()
    async def ironcad_connect_parts(
        names: Optional[list] = None,
        indices: Optional[list] = None,
        assembly_name: Optional[str] = None,
    ) -> dict:
        """Group parts into an ASSEMBLY so they move together (WRITE).

        This is the programmatic equivalent of selecting parts and assembling them
        — the true "connect" that makes parts a single linked group (verified:
        IZSceneDoc.AssembleElements, API_NOTES §9b). Identify the parts to group
        via `names` (top-level part NAMES) and/or `indices` (top-level indices,
        as returned by ironcad_list_parts) — combine both if useful; together they
        must resolve to 2+ DISTINCT parts.

        Prefer `indices` for symmetric/stock catalog frames: identical members
        (e.g. 4 posts cut to the same length) share the same auto-BOM name, which
        makes plain `names` resolution refuse as ambiguous (by design — see
        ironcad_add_catalog_part's BOM-naming warning: renaming to disambiguate
        would destroy BOM tracking). `indices` sidesteps that entirely.

        `assembly_name` optionally renames the created assembly. Requires
        read_write mode; backs up first. Refuses on <2 distinct parts or any
        missing/ambiguous name (when using `names` without a disambiguating
        index) or out-of-range index. Returns {assembly, children, count,
        backup_path}.

        Use this AFTER positioning the parts (e.g. via relative_to/offset): place
        them where they connect, then group them so later moves keep them together.
        """
        state = get_state()

        def work():
            require_write_mode("ironcad_connect_parts")
            wanted_names = [str(n) for n in (names or [])]
            wanted_indices = [int(i) for i in (indices or [])]
            if len(wanted_names) + len(wanted_indices) < 2:
                raise RuntimeError(
                    "Pass at least two parts to connect, via `names` and/or `indices`."
                )
            # Resolve every reference to a top-level element (fail before mutating).
            items = []
            for i, el in enumerate(state.iter_top_elements()):
                try:
                    nm = str(el.Name)
                except Exception:  # noqa: BLE001
                    nm = f"<element {i}>"
                items.append(NamedItem(i, nm, el))
            by_index = {it.index: it for it in items}

            elements = []
            resolved_names = []
            seen_indices: set[int] = set()

            def _add(chosen: NamedItem) -> None:
                if chosen.index in seen_indices:
                    return  # same part referenced twice (once by name, once by index)
                seen_indices.add(chosen.index)
                elements.append(chosen.obj)
                resolved_names.append(chosen.name)

            for idx in wanted_indices:
                if idx not in by_index:
                    raise RuntimeError(
                        f"No part at index {idx}. Available indices: "
                        f"{sorted(by_index)}."
                    )
                _add(by_index[idx])
            for n in wanted_names:
                _add(resolve_by_name(items, n))

            if len(elements) < 2:
                raise RuntimeError(
                    "`names`/`indices` resolved to fewer than two distinct parts."
                )
            backup_path = backup_active_doc(state.active_doc_name())

            # Verified: a plain Python list of IZElement pointers marshals to the
            # VARIANT array AssembleElements expects.
            asm = state.scene().AssembleElements(list(elements))

            asm_el = asm.QueryInterface(state.ICAPI.IZElement)
            if assembly_name:
                try:
                    asm_el.Name = assembly_name
                except Exception:  # noqa: BLE001
                    _logger.warning("Could not rename assembly to %r", assembly_name)
            try:
                state.scene().Update()
            except Exception:  # noqa: BLE001
                pass

            result_name = None
            child_count = None
            try:
                result_name = str(asm_el.Name)
            except Exception:  # noqa: BLE001
                pass
            try:
                child_count = int(asm_el.GetChildrenCount())
            except Exception:  # noqa: BLE001
                pass
            return {"assembly": result_name, "children": resolved_names,
                    "count": child_count, "backup_path": backup_path}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "names": names, "indices": indices}

    @mcp.tool()
    async def ironcad_set_anchor(
        name: str,
        position: Optional[list] = None,
        behavior: Optional[Any] = None,
        index: Optional[int] = None,
    ) -> dict:
        """Set a part's ANCHOR — the blue/white sphere — position and/or behavior (WRITE).

        The anchor is the point IronCAD uses to place and snap a part. VERIFIED
        live (API_NOTES §9b).
          * `position` = anchor location [x,y,z] in the part's LOCAL coordinates
            (metres). Sets it via GetAnchorTransform -> SetTranslation ->
            SetAnchorTransform.
          * `behavior` = how the anchor behaves; an int 0..3 or a name:
            'move_freely'(0), 'fixed_position'(1), 'attached_to_surface'(2),
            'slide_along_surface'(3).

        Resolves by NAME (index fallback). Requires read_write mode; backs up
        first. Returns {name, anchor_xyz_m, behavior, behavior_name}.
        """
        state = get_state()

        def work():
            require_write_mode("ironcad_set_anchor")
            if position is None and behavior is None:
                raise RuntimeError("Nothing to do: pass `position` and/or `behavior`.")
            code = _coerce_behavior(behavior) if behavior is not None else None
            backup_path = backup_active_doc(state.active_doc_name())
            chosen = _resolve_top_element(state, name, index)
            se = state.scene_element(chosen.obj)
            if position and len(position) >= 3:
                m = se.GetAnchorTransform()
                m.SetTranslation(float(position[0]), float(position[1]), float(position[2]))
                se.SetAnchorTransform(m)
            if code is not None:
                se.AnchorBehavior = code
            try:
                state.scene().Update()
            except Exception:  # noqa: BLE001
                pass
            anchor_xyz = None
            try:
                anchor_xyz = [float(se.AnchorTransformComponentValue[t]) for t in range(3)]
            except Exception:  # noqa: BLE001
                pass
            behav = None
            try:
                behav = int(se.AnchorBehavior)
            except Exception:  # noqa: BLE001
                pass
            return {"name": chosen.name, "anchor_xyz_m": anchor_xyz,
                    "behavior": behav,
                    "behavior_name": _ANCHOR_BEHAVIOR_NAMES.get(behav),
                    "backup_path": backup_path}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "name": name}

    @mcp.tool()
    async def ironcad_save(path: Optional[str] = None, overwrite: bool = False) -> dict:
        """Save the active document (WRITE).

        With no `path`, saves in place (the document must already have a file).
        With `path`, saves to that .ics path; saving over a DIFFERENT existing
        file requires overwrite=True (no silent overwrites). Requires read_write
        mode; backs up the current on-disk file first. Returns {saved_to}.
        """
        state = get_state()

        def work():
            require_write_mode("ironcad_save")
            current = state.active_doc_name()
            backup_path = backup_active_doc(current)
            z_ignore = getattr(state.ICAPI, "Z_LINKS_IGNORE", 5)
            if not path:
                # In-place save via IZDoc.Save().
                doc = state.active_doc()
                if not (current and os.path.isfile(current)):
                    raise RuntimeError(
                        "This document has never been saved; provide a `path` to "
                        "save it to a new .ics file."
                    )
                doc.Save()
                return {"saved_to": current, "backup_path": backup_path}
            # Save-as to a path.
            target = os.path.abspath(path)
            if os.path.isfile(target) and os.path.abspath(current or "") != target and not overwrite:
                raise RuntimeError(
                    f"Refusing to overwrite existing file '{target}'. Pass "
                    f"overwrite=True to allow it."
                )
            state.scene().SaveAs(target, z_ignore, bool(overwrite) or not os.path.isfile(target))
            return {"saved_to": target, "backup_path": backup_path}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "path": path}

    @mcp.tool()
    async def ironcad_save_copy(path: str, overwrite: bool = False) -> dict:
        """Save a COPY of the active document to `path`, leaving the original open.

        Requires read_write mode. Saving over a different existing file requires
        overwrite=True. Returns {saved_to}.
        """
        state = get_state()

        def work():
            require_write_mode("ironcad_save_copy")
            target = os.path.abspath(path)
            if os.path.isfile(target) and not overwrite:
                raise RuntimeError(
                    f"Refusing to overwrite existing file '{target}'. Pass "
                    f"overwrite=True to allow it."
                )
            z_ignore = getattr(state.ICAPI, "Z_LINKS_IGNORE", 5)
            state.scene().SaveAsCopy(target, z_ignore, True)
            return {"saved_to": target}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "path": path}
