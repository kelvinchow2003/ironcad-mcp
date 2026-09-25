"""Tier C — catalog tools (spec §6, M3): the core of the end goal.

`ironcad_list_catalog_parts` gives Claude the real vocabulary of part names to
plan against; `ironcad_add_catalog_part` is the keystone write that instantiates
a named catalog part into the scene (verified path: IZCatalogEntry.InsertElement).

Writes are gated on read_write mode and take a backup first (spec §7).
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .com_worker import run_on_com
from .connection import get_state
from .logging_setup import get_logger
from .safety import (
    NamedItem,
    NameResolutionError,
    backup_active_doc,
    require_write_mode,
    resolve_by_name,
)

_logger = get_logger()


def _iter_catalogs(state):
    cmgr = state.catalog_mgr()
    count = int(cmgr.Count)
    for i in range(count):
        try:
            yield i, cmgr.Catalog[i]
        except Exception:  # noqa: BLE001
            continue


def _find_catalog(state, catalog_name: Optional[str]):
    """Return a single IZCatalog by name, or the active catalog if name is None."""
    if not catalog_name:
        return state.catalog_mgr().ActiveCatalog
    matches = []
    for _, cat in _iter_catalogs(state):
        try:
            if str(cat.Name).lower() == catalog_name.lower():
                matches.append(cat)
        except Exception:  # noqa: BLE001
            continue
    if not matches:
        avail = ", ".join(str(c.Name) for _, c in _iter_catalogs(state))
        raise NameResolutionError(f"No catalog named '{catalog_name}'. Loaded: {avail}.")
    return matches[0]


def _find_entry(state, part_name: str, catalog_name: Optional[str]):
    """Resolve a catalog entry by part name. Returns (catalog, entry).

    Searches the named catalog if given, else ALL catalogs. Refuses ambiguity.
    """
    hits = []  # (catalog, entry)
    catalogs = ([_find_catalog(state, catalog_name)] if catalog_name
                else [c for _, c in _iter_catalogs(state)])
    for cat in catalogs:
        try:
            for j in range(int(cat.EntryCount)):
                e = cat.Entry[j]
                try:
                    if str(e.Name) == part_name:
                        hits.append((cat, e))
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
    if len(hits) == 1:
        return hits[0]
    if not hits:
        # case-insensitive retry
        for cat in catalogs:
            try:
                for j in range(int(cat.EntryCount)):
                    e = cat.Entry[j]
                    if str(e.Name).lower() == part_name.lower():
                        hits.append((cat, e))
            except Exception:  # noqa: BLE001
                continue
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise NameResolutionError(
            f"No catalog part named '{part_name}'"
            + (f" in catalog '{catalog_name}'." if catalog_name else " in any loaded catalog.")
        )
    where = ", ".join(sorted({str(c.Name) for c, _ in hits}))
    raise NameResolutionError(
        f"Ambiguous part name '{part_name}': found in multiple catalogs ({where}). "
        f"Pass the 'catalog' argument to disambiguate."
    )


# Axis codes for IZMathMatrix.SetRotation(nAxis, dDegrees, vbApplyToCurrent).
_AXES = (0, 1, 2)  # x, y, z


def _move_element(state, element, position, rotation_deg=None) -> Optional[list]:
    """Set an element's transform via a matrix (non-modal path).

    Applies optional rotation ([rx,ry,rz] deg about X,Y,Z) then absolute
    translation, via GetPositionTransform -> SetRotation/SetTranslation ->
    SetPositionTransform. Returns the resulting [x,y,z].
    """
    se = state.scene_element(element)
    m = se.GetPositionTransform()          # IZMathMatrix (keeps orientation)
    if rotation_deg:
        for axis, deg in zip(_AXES, list(rotation_deg)[:3]):
            if deg:
                m.SetRotation(axis, float(deg), True)
    if position and len(position) >= 3:
        m.SetTranslation(float(position[0]), float(position[1]), float(position[2]))
    se.SetPositionTransform(m)
    try:
        return [float(se.PositionTransformComponentValue[t]) for t in range(3)]
    except Exception:  # noqa: BLE001
        return list(position[:3]) if position else None


def _element_position(state, element) -> list:
    """Read a top-level element's translation [x,y,z] in metres (verified read)."""
    se = state.scene_element(element)
    return [float(se.PositionTransformComponentValue[t]) for t in range(3)]


def _resolve_target_position(state, relative_to: str) -> list:
    """Return the [x,y,z] translation (metres) of an existing part, by NAME.

    This is the anchor for RELATIVE placement: instead of guessing a part's
    absolute world coordinate, place it at an already-placed part's position
    plus an offset. Refuses on ambiguous/missing names (never guesses).
    """
    items = []
    for i, el in enumerate(state.iter_top_elements()):
        try:
            nm = str(el.Name)
        except Exception:  # noqa: BLE001
            nm = f"<element {i}>"
        items.append(NamedItem(i, nm, el))
    chosen = resolve_by_name(items, relative_to)
    return _element_position(state, chosen.obj)


def _apply_parameters(state, element, parameters: dict) -> dict:
    """Set driving parameters on a freshly placed element; return {param: {old,new}|error}.

    Verified path (API_NOTES §7): IZSceneElement.ParameterMgr ->
    GetParameterByName -> .Value = float. Regeneration is left to the caller so a
    batch can regen once at the end.
    """
    results: dict = {}
    se = state.scene_element(element)
    pmgr = se.ParameterMgr
    for pname, pval in parameters.items():
        try:
            p = pmgr.GetParameterByName(str(pname))
            old = None
            try:
                old = float(p.Value)
            except Exception:  # noqa: BLE001
                pass
            p.Value = float(pval)
            results[str(pname)] = {"old": old, "new": float(pval)}
        except Exception as exc:  # noqa: BLE001
            results[str(pname)] = {"error": str(exc)}
    return results


def _place_one(state, spec: dict) -> dict:
    """Instantiate + position + name + parameterize ONE catalog part.

    See :func:`_place_one_ex` for the full contract; this wrapper drops the
    raw inserted element for callers that only need the summary dict.
    """
    created, _element = _place_one_ex(state, spec)
    return created


def _place_one_ex(state, spec: dict) -> tuple[dict, Any]:
    """Same contract as :func:`_place_one`, but also returns the raw inserted
    IZElement — for callers (e.g. ironcad_add_catalog_assembly) that need to
    QueryInterface it further after placement.

    `spec` keys: name (required), catalog?, position? ([x,y,z] abs metres),
    relative_to? (existing part name — anchor), offset? ([dx,dy,dz] metres),
    instance_name?, parameters? ({name: value}).

    Placement precedence: if `relative_to` is given, final position =
    anchor_position + (offset or [0,0,0]); otherwise `position` (absolute) is
    used if given; otherwise the part stays at InsertElement's default spot.

    Does NOT gate mode or take a backup — the caller owns that so a batch backs
    up once. Returns (created_dict, element); never raises for placement
    issues; a bad NAME resolution does raise so the caller can surface it.
    """
    name = spec["name"]
    cat, entry = _find_entry(state, name, spec.get("catalog"))

    # Resolve a relative anchor BEFORE inserting (so a missing anchor fails
    # before we mutate the scene).
    final_pos = None
    if spec.get("relative_to"):
        anchor = _resolve_target_position(state, spec["relative_to"])
        off = spec.get("offset") or [0.0, 0.0, 0.0]
        final_pos = [anchor[t] + float(off[t] if t < len(off) else 0.0) for t in range(3)]
    elif spec.get("position") and len(spec["position"]) >= 3:
        final_pos = [float(spec["position"][t]) for t in range(3)]

    element = entry.InsertElement()  # -> IZElement (keystone)

    rotation_deg = spec.get("rotation_deg")
    if final_pos is not None or rotation_deg:
        try:
            _move_element(state, element, final_pos, rotation_deg)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Could not set transform for %s: %s", name, exc)

    if spec.get("instance_name"):
        try:
            element.Name = spec["instance_name"]
        except Exception:  # noqa: BLE001
            _logger.warning("Could not set instance name %r", spec["instance_name"])

    param_results = None
    if spec.get("parameters"):
        try:
            param_results = _apply_parameters(state, element, spec["parameters"])
        except Exception as exc:  # noqa: BLE001
            param_results = {"error": str(exc)}

    created = {
        "name": str(getattr(element, "Name", "?")),
        "catalog": str(cat.Name),
        "type_code": (int(element.Type) if _safe(element, "Type") else None),
        "id": (int(element.Id) if _safe(element, "Id") else None),
    }
    try:
        created["position_xyz_m"] = _element_position(state, element)
    except Exception:  # noqa: BLE001
        created["position_xyz_m"] = None
    if param_results is not None:
        created["parameters"] = param_results
    if spec.get("relative_to"):
        created["placed_relative_to"] = spec["relative_to"]
    return created, element


def _bbox_dict(obj) -> Optional[dict]:
    """Bbox dict (dims/min/max/center, global metres) from anything exposing
    GetBoundingBox(False) — works for both IZPart and IZAssembly."""
    try:
        bb = [float(v) for v in obj.GetBoundingBox(False)]
    except Exception:  # noqa: BLE001
        return None
    return {
        "dims_m": [round(bb[k + 3] - bb[k], 6) for k in range(3)],
        "min_m": [round(bb[k], 6) for k in range(3)],
        "max_m": [round(bb[k + 3], 6) for k in range(3)],
        "center_m": [round((bb[k] + bb[k + 3]) / 2.0, 6) for k in range(3)],
    }


# ---- ironcad_build_panel: tile -> Boolean-UNITE -> Boolean-SUBTRACT -------
# Recipe verified live in scripts/build_hoist_cover_full.py; promoted here to
# a first-class tool per PRODUCTION_READINESS_PLAN Phase 2.1 (previously only
# reachable via the raw ironcad_execute_api_script escape hatch).

_PANEL_TILE_M = 0.5  # native PAN_DIST panel stock size (500x500mm)
_Z_UNITE = 1     # Boolean enum (__MIDL___MIDL_itf_ICApiIronCAD_0000_0000_0004)
_Z_SUBTRACT = 2  # same enum — verified live (see ironcad-mcp-project memory)

# Rotation recipes verified live 2026-09-19 for this Boolean-built/PAN_DIST
# part family (each axis its OWN GetPositionTransform/SetPositionTransform
# round-trip — combining two axes on one matrix silently no-ops the first).
# Maps orientation name -> (rotation steps, meaning of local (w,h,thickness)
# in the resulting GLOBAL (X,Y,Z)).
_PANEL_ORIENTATIONS = {
    "flat_xy": ([], "w=X, h=Y, thickness=Z"),
    "face_xz": ([(0, 90.0), (1, 90.0)], "w=X, thickness=Y, h=Z"),
    "face_yz": ([(2, 90.0), (1, 90.0)], "thickness=X, w=Y, h=Z"),
}


def _panel_tile(state, entry, x: float, y: float, thickness_m: float):
    """Insert one native 500x500mm panel tile at LOCAL (x,y); set thickness."""
    el = entry.InsertElement()
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    m.MakeIdentity()
    m.SetTranslation(float(x), float(y), 0.0)
    se.SetPositionTransform(m)
    block = el.GetFirstChild()
    ef = block.QueryInterface(state.ICAPI.IZExtrudeFeature)
    ef.ExtrudeDistance[0] = float(thickness_m)
    try:
        ef.ExtrudeDistance[1] = 0.0
    except Exception:  # noqa: BLE001
        pass
    el.QueryInterface(state.ICAPI.IZPart).Regenerate()
    return el


def _panel_bbox(state, el) -> list:
    part = el.QueryInterface(state.ICAPI.IZPart)
    return [float(v) for v in part.GetBoundingBox(False)]


def _panel_trim_axis_x(state, entry, el, target_w: float, row_y: float, thickness_m: float):
    part = el.QueryInterface(state.ICAPI.IZPart)
    # Cutting tool deliberately thicker than the target so the trim is a
    # clean full-depth cut regardless of the panel's own thickness.
    tool = _panel_tile(state, entry, target_w, row_y, thickness_m * 1.5)
    part.Boolean(tool.QueryInterface(state.ICAPI.IZPart), _Z_SUBTRACT)


def _build_flat_panel(state, entry, target_w: float, target_h: float, thickness_m: float):
    """Tile -> weld (UNITE) -> trim (SUBTRACT) to an exact W x H x thickness
    panel. Returns (panel_element, tile_count)."""
    n_cols = max(1, math.ceil(round(target_w / _PANEL_TILE_M, 6)))
    n_rows = max(1, math.ceil(round(target_h / _PANEL_TILE_M, 6)))
    tile_count = 0

    def build_row(row_y: float):
        nonlocal tile_count
        tiles = [_panel_tile(state, entry, c * _PANEL_TILE_M, row_y, thickness_m)
                 for c in range(n_cols)]
        tile_count += len(tiles)
        row_el = tiles[0]
        row_part = row_el.QueryInterface(state.ICAPI.IZPart)
        for tl in tiles[1:]:
            row_part.Boolean(tl.QueryInterface(state.ICAPI.IZPart), _Z_UNITE)
        if n_cols * _PANEL_TILE_M > target_w + 1e-9:
            _panel_trim_axis_x(state, entry, row_el, target_w, row_y, thickness_m)
            tile_count += 1
        return row_el

    rows = [build_row(r * _PANEL_TILE_M) for r in range(n_rows)]
    panel_el = rows[0]
    panel_part = panel_el.QueryInterface(state.ICAPI.IZPart)
    for r in rows[1:]:
        panel_part.Boolean(r.QueryInterface(state.ICAPI.IZPart), _Z_UNITE)
    if n_rows * _PANEL_TILE_M > target_h + 1e-9:
        y_tool_row = build_row(target_h)
        panel_part.Boolean(y_tool_row.QueryInterface(state.ICAPI.IZPart), _Z_SUBTRACT)
        tile_count += 1
    return panel_el, tile_count


def _rotate_chain(state, el, steps) -> None:
    """Apply rotation `steps` ([(axis, degrees), ...]), each its OWN
    GetPositionTransform/SetPositionTransform round-trip (see
    _PANEL_ORIENTATIONS docstring note above for why)."""
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    m.MakeIdentity()
    se.SetPositionTransform(m)
    for axis, deg in steps:
        m = se.GetPositionTransform()
        m.SetRotation(int(axis), float(deg), True)
        se.SetPositionTransform(m)


def _translate_min_to(state, el, target_min: list) -> None:
    """Shift `el` so its measured bbox MIN corner lands at `target_min` —
    measured, not predicted, since rotation can flip which corner is min."""
    cur = _panel_bbox(state, el)
    se = state.scene_element(el)
    m = se.GetPositionTransform()
    tx, ty, tz = m.GetTranslation()
    dx, dy, dz = (target_min[k] - cur[k] for k in range(3))
    m.SetTranslation(tx + dx, ty + dy, tz + dz)
    se.SetPositionTransform(m)


def register(mcp) -> None:  # noqa: ANN001

    @mcp.tool()
    async def ironcad_list_catalogs() -> dict:
        """List the catalogs currently loaded in IronCAD.

        Returns {count, catalogs:[{index, name, entry_count, active}]}. Read-only.
        """
        state = get_state()

        def work():
            cmgr = state.catalog_mgr()
            active_name = None
            try:
                active_name = str(cmgr.ActiveCatalog.Name)
            except Exception:  # noqa: BLE001
                pass
            cats = []
            for i, cat in _iter_catalogs(state):
                nm = str(cat.Name)
                cats.append({
                    "index": i,
                    "name": nm,
                    "entry_count": int(cat.EntryCount),
                    "active": (nm == active_name),
                })
            return {"count": len(cats), "catalogs": cats}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    @mcp.tool()
    async def ironcad_list_catalog_parts(catalog: Optional[str] = None) -> dict:
        """List catalog part NAMES available to drop into the scene.

        Pass `catalog` to restrict to one catalog; omit to list the active
        catalog. These names are the exact vocabulary to use with
        ironcad_add_catalog_part — do not invent names. Returns
        {catalog, count, parts:[{index, name, is_group}]}. Read-only.
        """
        state = get_state()

        def work():
            cat = _find_catalog(state, catalog)
            parts = []
            for j in range(int(cat.EntryCount)):
                e = cat.Entry[j]
                grp = None
                try:
                    grp = bool(e.IsCatalogGroup())
                except Exception:  # noqa: BLE001
                    pass
                parts.append({"index": j, "name": str(e.Name), "is_group": grp})
            return {"catalog": str(cat.Name), "count": len(parts), "parts": parts}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    @mcp.tool()
    async def ironcad_get_catalog_part_info(name: str, catalog: Optional[str] = None) -> dict:
        """Best-effort details for a catalog part (parameters/config), by name.

        Returns {name, catalog, is_group, class_id, parameters:[...]} where
        available. Some fields depend on the catalog item type. Read-only.
        """
        state = get_state()

        def work():
            cat, entry = _find_entry(state, name, catalog)
            info: dict = {"name": str(entry.Name), "catalog": str(cat.Name)}
            try:
                info["is_group"] = bool(entry.IsCatalogGroup())
            except Exception:  # noqa: BLE001
                info["is_group"] = None
            for attr, key in (("ClassID", "class_id"), ("ItemCreatorType", "item_creator_type")):
                try:
                    info[key] = str(getattr(entry, attr))
                except Exception:  # noqa: BLE001
                    pass
            # Parameters of the underlying object, if exposed.
            params = []
            try:
                obj = entry.Object
                pmgr = obj.QueryInterface(state.ICAPI.IZSceneElement).ParameterMgr
                for i in range(int(pmgr.Count)):
                    p = pmgr.GetParameter(i)
                    params.append(str(getattr(p, "Name", f"param_{i}")))
            except Exception:  # noqa: BLE001
                pass
            info["parameters"] = params
            info["note"] = ("Full parameter/config introspection for catalog items "
                            "is partial (API_NOTES open); instantiate to read live params.")
            return info

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "name": name}

    @mcp.tool()
    async def ironcad_add_catalog_part(
        name: str,
        catalog: Optional[str] = None,
        position: Optional[list] = None,
        instance_name: Optional[str] = None,
        relative_to: Optional[str] = None,
        offset: Optional[list] = None,
        parameters: Optional[dict] = None,
        rotation_deg: Optional[list] = None,
    ) -> dict:
        """Instantiate a catalog part BY NAME into the active scene (keystone write).

        WRITE tool: requires IRONCAD_MCP_MODE=read_write and takes a timestamped
        backup first (aborts if the backup fails).

        Positioning (prefer RELATIVE — it avoids guessing world coordinates and
        the wasteful place-capture-move loop):
          * `relative_to` = the NAME of an already-placed part to anchor against;
            the new part lands at that part's position + `offset` ([dx,dy,dz]
            metres, default [0,0,0]). This is the efficient way to connect a part
            to another (e.g. drop a connector at a beam's origin, then offset).
          * `position` = an absolute [x,y,z] in metres. Only used when
            `relative_to` is not given. Guessing absolutes is discouraged.

        `instance_name` optionally renames the created element — AVOID this for
        stock/BOM parts (e.g. PIL4040SNN_): IronCAD auto-derives the element's
        Name from its real geometry on insert/cut (PIL4040SNN_ -> PIL4040SNN500,
        or PIL4040SNN420 after a Boolean cut) and the BOM is built from that
        name; overwriting it breaks the BOM entry PERMANENTLY — verified live
        that it does not come back even after RegenerateParts. Only use
        `instance_name` for elements that aren't meant to be individually
        BOM-tracked (e.g. an assembly/grouping node), or reference parts by
        `id`/position instead of renaming them. `parameters` is an
        optional {param_name: value} map applied in the SAME call (saves a
        round-trip vs. ironcad_set_part_parameter). `rotation_deg` is an optional
        [rx,ry,rz] rotation (degrees about X,Y,Z) applied before positioning —
        use it to orient an extrusion/profile along the axis you need. Returns
        {created:{name,catalog,type_code,id,position_xyz_m,parameters?}, backup_path}.

        Refuses on ambiguous/missing part or anchor names. This is the
        programmatic equivalent of dragging a part from the catalog browser.
        """
        state = get_state()
        spec = {
            "name": name, "catalog": catalog, "position": position,
            "instance_name": instance_name, "relative_to": relative_to,
            "offset": offset, "parameters": parameters,
            "rotation_deg": rotation_deg,
        }

        def work():
            require_write_mode("ironcad_add_catalog_part")
            # Resolve the entry (and any anchor) first so we fail before backing
            # up on a bad name.
            _find_entry(state, name, catalog)
            if relative_to:
                _resolve_target_position(state, relative_to)
            backup_path = backup_active_doc(state.active_doc_name())

            created = _place_one(state, spec)

            try:
                state.scene().RegenerateParts(True)
            except Exception:  # noqa: BLE001
                pass
            try:
                state.scene().Update()
            except Exception:  # noqa: BLE001
                pass
            return {"created": created, "backup_path": backup_path,
                    "catalog": created.get("catalog")}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "name": name}

    @mcp.tool()
    async def ironcad_build_parts(parts: list) -> dict:
        """Place MANY catalog parts in ONE call — the credit-efficient build path.

        WRITE tool: requires IRONCAD_MCP_MODE=read_write. Takes ONE backup up
        front, then instantiates each item, regenerates + updates ONCE at the end.
        Prefer this over many ironcad_add_catalog_part calls: it collapses N
        round-trips (and N verification captures) into one.

        `parts` is an ordered list of dicts, each with:
          {name (required), catalog?, instance_name?,
           relative_to? (name of an EARLIER part in this list, or an existing
                         scene part, to anchor against),
           offset? ([dx,dy,dz] metres), position? ([x,y,z] abs metres, fallback),
           rotation_deg? ([rx,ry,rz] degrees about X,Y,Z — orient extrusions),
           parameters? ({param_name: value})}

        CAUTION — `instance_name` vs. BOM naming: for stock/BOM catalog parts
        (e.g. PIL4040SNN_) IronCAD auto-derives the element's Name from its
        real geometry on insert/cut (PIL4040SNN_ -> PIL4040SNN500, or
        PIL4040SNN420 after a Boolean cut) and the BOM is built from that
        name; setting `instance_name` overwrites it and the BOM entry is lost
        PERMANENTLY (does not come back even after a regenerate). Only set
        `instance_name` when you specifically need to `relative_to`-reference
        that part later in the SAME batch (duplicate default stock names are
        ambiguous for name resolution) or don't care about its BOM identity —
        otherwise leave it unset and let the part keep its auto BOM name.

        Order matters: a part may be placed `relative_to` a part created earlier
        in the same list. Per-item failures are captured and reported without
        aborting the rest. Returns
        {count, placed:[created...], errors:[{index,name,error}], backup_path}.
        Verify the whole result with a SINGLE ironcad_capture_view afterward.
        """
        state = get_state()
        items = list(parts or [])

        def work():
            require_write_mode("ironcad_build_parts")
            if not items:
                raise RuntimeError("`parts` is empty — nothing to build.")
            # Validate every item has a resolvable NAME before mutating anything.
            for i, spec in enumerate(items):
                if not isinstance(spec, dict) or not spec.get("name"):
                    raise RuntimeError(f"parts[{i}] must be a dict with a 'name'.")
                _find_entry(state, spec["name"], spec.get("catalog"))
            backup_path = backup_active_doc(state.active_doc_name())

            placed: list = []
            errors: list = []
            for i, spec in enumerate(items):
                try:
                    placed.append(_place_one(state, spec))
                except Exception as exc:  # noqa: BLE001
                    errors.append({"index": i, "name": spec.get("name"),
                                   "error": str(exc)})
            # Regenerate + update ONCE for the whole batch.
            try:
                state.scene().RegenerateParts(True)
            except Exception:  # noqa: BLE001
                pass
            try:
                state.scene().Update()
            except Exception:  # noqa: BLE001
                pass
            return {"count": len(placed), "placed": placed, "errors": errors,
                    "backup_path": backup_path}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    @mcp.tool()
    async def ironcad_add_catalog_assembly(
        name: str,
        catalog: Optional[str] = None,
        position: Optional[list] = None,
        instance_name: Optional[str] = None,
        relative_to: Optional[str] = None,
        offset: Optional[list] = None,
        parameters: Optional[dict] = None,
        rotation_deg: Optional[list] = None,
    ) -> dict:
        """Instantiate a COMPOUND catalog entry (a multi-part assembly) BY NAME (WRITE).

        Some catalog entries are themselves small assemblies bundling several
        parts — e.g. `PAN___P_N___+PIN4521` (a resizable panel + sizing
        helper) or `PAN___P_N___+CAL4515SNN___+CAN4501` (a panel + the REAL
        Robotunits panel-retention-rail + connector hardware used in
        hand-built designs). `ironcad_add_catalog_part` also technically
        works for these (InsertElement is the same call either way), but
        this tool additionally handles what these compound entries need that
        a plain leaf part doesn't:
          * Applies `parameters` (their real names vary by entry — verified
            live: `Gesamtbreite`/`Gesamtlaenge` for the PIN4521 family,
            `Breite`/`Hoehe` for the CAL+CAN family; use
            ironcad_get_catalog_part_info or ironcad_list_children after a
            plain insert to discover the names for an entry not yet
            documented) and regenerates via the ASSEMBLY-specific path
            (QueryInterface to IZAssembly, then `RegenerateParts(True)`) —
            verified live 2026-09-24: these entries do NOT rebuild from a
            plain IZPart.Regenerate() (most don't even QI to IZPart at all)
            or from a scene-wide regenerate; they need their OWN
            IZAssembly.RegenerateParts(True) call after the parameter is set.
          * Reads back the resulting OVERALL bbox via
            IZAssembly.GetBoundingBox — works directly, no child traversal
            needed — so you can verify the real size immediately. Use
            ironcad_list_children afterward if you need the individual
            bundled sub-parts (e.g. to confirm what hardware it placed).

        Positioning (`relative_to`+`offset` preferred over absolute
        `position`) and `rotation_deg` work exactly like
        ironcad_add_catalog_part. `instance_name` renames the assembly
        container itself — safe here, unlike on a leaf stock part (an
        assembly node isn't individually length-tracked in the BOM the same
        way; see ironcad_connect_parts' docstring for the general rule).

        Refuses if the resolved catalog entry does not QueryInterface to
        IZAssembly (i.e. it's actually a plain leaf part) — use
        ironcad_add_catalog_part for those instead. Requires read_write
        mode; backs up first. Returns {created:{name,catalog,type_code,id,
        position_xyz_m,parameters?}, bbox_m:{dims_m,min_m,max_m,center_m}|
        null, backup_path}.
        """
        state = get_state()
        place_spec = {
            "name": name, "catalog": catalog, "position": position,
            "instance_name": instance_name, "relative_to": relative_to,
            "offset": offset, "parameters": None,  # applied after regen below
            "rotation_deg": rotation_deg,
        }

        def work():
            require_write_mode("ironcad_add_catalog_assembly")
            _find_entry(state, name, catalog)
            if relative_to:
                _resolve_target_position(state, relative_to)
            backup_path = backup_active_doc(state.active_doc_name())

            created, element = _place_one_ex(state, place_spec)

            try:
                asm = element.QueryInterface(state.ICAPI.IZAssembly)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"'{created.get('name')}' does not QueryInterface to "
                    f"IZAssembly (type_code={created.get('type_code')}) — it "
                    f"is not a compound catalog assembly. Use "
                    f"ironcad_add_catalog_part instead."
                ) from exc

            if parameters:
                param_results = _apply_parameters(state, element, parameters)
                created["parameters"] = param_results
                try:
                    asm.RegenerateParts(True)
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        "IZAssembly.RegenerateParts failed for %r after "
                        "setting parameters: %s", created.get("name"), exc,
                    )

            try:
                state.scene().Update()
            except Exception:  # noqa: BLE001
                pass

            bbox_m = _bbox_dict(asm)
            return {"created": created, "bbox_m": bbox_m, "backup_path": backup_path}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "name": name}

    @mcp.tool()
    async def ironcad_build_panel(
        target_w_m: float,
        target_h_m: float,
        thickness_m: float,
        position: Optional[list] = None,
        relative_to: Optional[str] = None,
        offset: Optional[list] = None,
        orientation: str = "flat_xy",
        catalog_entry: str = "PAN___P_N___",
        catalog: Optional[str] = "PAN_DIST",
        instance_name: Optional[str] = None,
    ) -> dict:
        """Build a flat panel of an ARBITRARY width x height x thickness (WRITE).

        First-class tool for the tile -> Boolean-UNITE -> Boolean-SUBTRACT
        recipe (previously only reachable via ad-hoc Python through the
        ironcad_execute_api_script escape hatch — see
        PRODUCTION_READINESS_PLAN Phase 2.1). Tiles native 500x500mm
        `catalog_entry` instances (default the generic PAN_DIST panel
        `PAN___P_N___`) to cover at least `target_w_m` x `target_h_m`, welds
        them into one piece, then trims the excess on each axis down to the
        exact target — verified live (scripts/build_hoist_cover_full.py;
        reproduced here as a reusable tool instead of per-build Python).

        PREFER `ironcad_add_catalog_assembly` INSTEAD when the panel sits in
        a rectangular T-slot framed opening and fits within a compound
        catalog entry's size range: `PAN___P_N___+PIN4521`
        (Gesamtbreite/Gesamtlaenge params) or
        `...+CAL4515SNN___+CAN4501` (Breite/Hoehe params — the REAL
        Robotunits panel-retention-rail hardware). Those auto-detail the
        panel with real hardware; this tool only ever produces a bare slab.
        Use this tool when no such compound entry fits, or the panel doesn't
        sit in a framed opening at all.

        `orientation` (rotation recipes verified live for this Boolean-built
        panel family — each axis its OWN transform round-trip, unlike the
        T-slot extrusions):
          * 'flat_xy' (default): local w=X, h=Y, thickness=Z.
          * 'face_xz': local w=X, thickness=Y, h=Z (panel stands upright,
            face in the global XZ plane).
          * 'face_yz': local thickness=X, w=Y, h=Z (face in the global YZ
            plane).

        Positioning: after building + orienting, the panel's MEASURED bbox
        min corner (never assumed — rotation can flip which corner is min)
        is translated to `position` (absolute) or `relative_to`+`offset`
        (anchor part's position + offset, preferred).

        Requires read_write mode; backs up first. Returns {name, dims_m,
        min_m, max_m, tile_count, orientation, backup_path}.
        """
        state = get_state()

        def work():
            require_write_mode("ironcad_build_panel")
            if orientation not in _PANEL_ORIENTATIONS:
                raise RuntimeError(
                    f"orientation must be one of {sorted(_PANEL_ORIENTATIONS)}, "
                    f"got {orientation!r}."
                )
            cat, entry = _find_entry(state, catalog_entry, catalog)
            final_pos = None
            if relative_to:
                anchor = _resolve_target_position(state, relative_to)
                off = offset or [0.0, 0.0, 0.0]
                final_pos = [anchor[t] + float(off[t] if t < len(off) else 0.0)
                             for t in range(3)]
            elif position and len(position) >= 3:
                final_pos = [float(position[t]) for t in range(3)]
            backup_path = backup_active_doc(state.active_doc_name())

            panel_el, tile_count = _build_flat_panel(
                state, entry, float(target_w_m), float(target_h_m), float(thickness_m)
            )
            steps, _meaning = _PANEL_ORIENTATIONS[orientation]
            if steps:
                _rotate_chain(state, panel_el, steps)
            if final_pos is not None:
                _translate_min_to(state, panel_el, final_pos)

            if instance_name:
                try:
                    panel_el.Name = instance_name
                except Exception:  # noqa: BLE001
                    _logger.warning("Could not set instance name %r on panel",
                                     instance_name)

            bb = _panel_bbox(state, panel_el)
            pname = None
            try:
                pname = str(panel_el.Name)
            except Exception:  # noqa: BLE001
                pass
            return {
                "name": pname,
                "dims_m": [round(bb[k + 3] - bb[k], 6) for k in range(3)],
                "min_m": [round(bb[k], 6) for k in range(3)],
                "max_m": [round(bb[k + 3], 6) for k in range(3)],
                "tile_count": tile_count,
                "orientation": orientation,
                "backup_path": backup_path,
            }

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}


def _safe(obj: Any, attr: str) -> bool:
    try:
        getattr(obj, attr)
        return True
    except Exception:  # noqa: BLE001
        return False
