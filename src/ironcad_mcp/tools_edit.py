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
        names: list,
        assembly_name: Optional[str] = None,
    ) -> dict:
        """Group parts into an ASSEMBLY so they move together (WRITE).

        This is the programmatic equivalent of selecting parts and assembling them
        — the true "connect" that makes parts a single linked group (verified:
        IZSceneDoc.AssembleElements, API_NOTES §9b). Pass 2+ existing part NAMES;
        they are grouped under a new assembly whose children move as one.

        `assembly_name` optionally renames the created assembly. Requires
        read_write mode; backs up first. Refuses on <2 names or any missing/
        ambiguous name. Returns {assembly, children, count, backup_path}.

        Use this AFTER positioning the parts (e.g. via relative_to/offset): place
        them where they connect, then group them so later moves keep them together.
        """
        state = get_state()

        def work():
            require_write_mode("ironcad_connect_parts")
            wanted = [str(n) for n in (names or [])]
            if len(wanted) < 2:
                raise RuntimeError("Pass at least two part names to connect.")
            # Resolve every name to a top-level element (fail before mutating).
            items = []
            for i, el in enumerate(state.iter_top_elements()):
                try:
                    nm = str(el.Name)
                except Exception:  # noqa: BLE001
                    nm = f"<element {i}>"
                items.append(NamedItem(i, nm, el))
            elements = []
            resolved_names = []
            for n in wanted:
                chosen = resolve_by_name(items, n)
                elements.append(chosen.obj)
                resolved_names.append(chosen.name)
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
            return {"error": str(exc), "names": names}

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
