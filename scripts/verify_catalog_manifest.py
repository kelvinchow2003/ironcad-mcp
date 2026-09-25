"""Live-check `src/ironcad_mcp/data/catalog_manifest.json` against the
currently-loaded IronCAD catalogs (PRODUCTION_READINESS_PLAN.md Phase 6.2).

Not run automatically (no test depends on it) — run it by hand after an
IronCAD/catalog update, or whenever a manifest entry's accuracy is in doubt:

    .venv\\Scripts\\python.exe scripts\\verify_catalog_manifest.py

For each catalog the manifest documents, checks live whether:
  * the catalog is actually loaded in this IronCAD session,
  * every "usable_entries" / entry-level part name still resolves via
    InsertElement() (inserted into a FRESH UNSAVED scratch scene, then the
    scene is closed again — never saved, never touches a real file),
  * every documented settable_params name is still present on that part's
    ParameterMgr.

Prints a drift report; does not modify the manifest file itself (a human
reviews and edits the JSON by hand after seeing what changed).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJ_ROOT, "src"))

os.environ.setdefault("IRONCAD_MCP_MODE", "read_write")
os.environ.setdefault("IRONCAD_MCP_BACKUP_DIR", os.path.join(PROJ_ROOT, ".verify_backups"))

from ironcad_mcp.catalog_manifest import load_manifest  # noqa: E402
from ironcad_mcp.com_worker import run_on_com, start_worker, stop_worker  # noqa: E402
from ironcad_mcp.connection import get_state  # noqa: E402


def _entry_names(catalog_data: dict) -> list[str]:
    """Pull every concrete part-name-looking key out of a manifest catalog
    entry (from `usable_entries`/`entries` dicts, ignoring prose-only keys)."""
    names: list[str] = []
    for key in ("usable_entries", "entries"):
        block = catalog_data.get(key)
        if isinstance(block, dict):
            names.extend(block.keys())
    return names


def _new_scene():
    import comtypes.gen.ICAPIIRONCADLib as ICAPI
    state = get_state()
    iapp = state.app.QueryInterface(ICAPI.IZIronCADApp)
    iapp.OpenNewScene(False, True, "")
    return state.active_doc_name(), state.active_doc()


def _check_one(catalog_name: str, part_name: str) -> dict:
    state = get_state()
    cmgr = state.catalog_mgr()
    cat = None
    for i in range(int(cmgr.Count)):
        c = cmgr.Catalog[i]
        if str(c.Name).lower() == catalog_name.lower():
            cat = c
            break
    if cat is None:
        return {"catalog": catalog_name, "part": part_name, "status": "CATALOG_NOT_LOADED"}
    entry = None
    for j in range(int(cat.EntryCount)):
        e = cat.Entry[j]
        try:
            if str(e.Name) == part_name:
                entry = e
                break
        except Exception:  # noqa: BLE001
            continue
    if entry is None:
        return {"catalog": catalog_name, "part": part_name, "status": "ENTRY_NOT_FOUND"}
    try:
        el = entry.InsertElement()
    except Exception as exc:  # noqa: BLE001
        return {"catalog": catalog_name, "part": part_name, "status": "INSERT_FAILED",
                "detail": str(exc)}
    params = []
    try:
        se = state.scene_element(el)
        pmgr = se.ParameterMgr
        for i in range(int(pmgr.Count)):
            try:
                params.append(str(pmgr.GetParameter(i).Name))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return {"catalog": catalog_name, "part": part_name, "status": "OK", "live_params": params}


async def main() -> None:
    from ironcad_mcp.server import mcp

    tool = mcp._tool_manager.get_tool("ironcad_status")
    st = await tool.run({}, convert_result=False)
    if not st.get("connected"):
        await mcp._tool_manager.get_tool("ironcad_attach").run({}, convert_result=False)

    manifest = load_manifest()
    report = []
    scene_name, doc = await run_on_com(_new_scene)
    print(f"Working in fresh unsaved scene: {scene_name} (will be closed, never saved)\n")
    try:
        for catalog_name, data in manifest.get("catalogs", {}).items():
            for part_name in _entry_names(data):
                result = await run_on_com(lambda c=catalog_name, p=part_name: _check_one(c, p))
                report.append(result)
                print(f"[{result['status']:16}] {catalog_name} :: {part_name}"
                      + (f" -- {result['detail']}" if result.get("detail") else ""))
    finally:
        def _close():
            try:
                doc.Close2(True)
            except Exception:  # noqa: BLE001
                pass
        await run_on_com(_close)

    drift = [r for r in report if r["status"] != "OK"]
    print(f"\n{len(report)} entries checked, {len(drift)} showing drift from the manifest.")
    if drift:
        print("Entries needing manifest review:")
        for r in drift:
            print(f"  - {r['catalog']} :: {r['part']} -> {r['status']}")


if __name__ == "__main__":
    start_worker()
    try:
        asyncio.run(main())
    finally:
        stop_worker()
