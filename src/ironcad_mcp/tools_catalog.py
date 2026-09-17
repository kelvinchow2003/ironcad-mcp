"""Tier C — catalog tools (spec §6, M3): the core of the end goal.

`ironcad_list_catalog_parts` gives Claude the real vocabulary of part names to
plan against; `ironcad_add_catalog_part` is the keystone write that instantiates
a named catalog part into the scene (verified path: IZCatalogEntry.InsertElement).

Writes are gated on read_write mode and take a backup first (spec §7).
"""

from __future__ import annotations

from typing import Any, Optional

from .com_worker import run_on_com
from .connection import get_state
from .logging_setup import get_logger
from .safety import (
    NameResolutionError,
    backup_active_doc,
    require_write_mode,
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


def _move_element(state, element, position) -> Optional[list]:
    """Set an element's absolute translation via a matrix (non-modal path).

    Uses GetPositionTransform -> SetTranslation -> SetPositionTransform rather
    than poking PositionTransformComponentValue. Returns the resulting [x,y,z].
    """
    se = state.scene_element(element)
    m = se.GetPositionTransform()          # IZMathMatrix (keeps orientation)
    m.SetTranslation(float(position[0]), float(position[1]), float(position[2]))
    se.SetPositionTransform(m)
    try:
        return [float(se.PositionTransformComponentValue[t]) for t in range(3)]
    except Exception:  # noqa: BLE001
        return list(position[:3])


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
    ) -> dict:
        """Instantiate a catalog part BY NAME into the active scene (keystone write).

        WRITE tool: requires IRONCAD_MCP_MODE=read_write and takes a timestamped
        backup first (aborts if the backup fails). `position` is optional
        [x,y,z] in metres; `instance_name` optionally renames the created element.
        Returns {created:{name,type,id,position_xyz_m}, backup_path}.

        Refuses on ambiguous/missing part names. This is the programmatic
        equivalent of dragging a part from the catalog browser.
        """
        state = get_state()

        def work():
            require_write_mode("ironcad_add_catalog_part")
            # Resolve first so we fail before backing up on a bad name.
            cat, entry = _find_entry(state, name, catalog)
            backup_path = backup_active_doc(state.active_doc_name())

            element = entry.InsertElement()  # -> IZElement (keystone)

            if position and len(position) >= 3:
                try:
                    _move_element(state, element, position)
                except Exception as exc:  # noqa: BLE001
                    _logger.warning("Could not set position: %s", exc)

            if instance_name:
                try:
                    element.Name = instance_name
                except Exception:  # noqa: BLE001
                    _logger.warning("Could not set instance name %r", instance_name)

            try:
                state.scene().Update()
            except Exception:  # noqa: BLE001
                pass

            created = {
                "name": str(getattr(element, "Name", "?")),
                "type_code": (int(element.Type) if _safe(element, "Type") else None),
                "id": (int(element.Id) if _safe(element, "Id") else None),
            }
            try:
                se = state.scene_element(element)
                created["position_xyz_m"] = [float(se.PositionTransformComponentValue[t])
                                             for t in range(3)]
            except Exception:  # noqa: BLE001
                created["position_xyz_m"] = None
            return {"created": created, "backup_path": backup_path,
                    "catalog": str(cat.Name)}

        try:
            return await run_on_com(work)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "name": name}


def _safe(obj: Any, attr: str) -> bool:
    try:
        getattr(obj, attr)
        return True
    except Exception:  # noqa: BLE001
        return False
