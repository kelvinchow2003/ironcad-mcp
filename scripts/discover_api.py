"""M1 API-discovery spike (spec §5) — VERIFIED read-only reproduction.

Run against a running IronCAD 2024 (API DLLs registered; see README) with a part
and the user's catalog open. Uses the verified QueryInterface path from
API_NOTES.md. Read-only: it does NOT call InsertElement or any mutation; the only
file it writes is a temp JPEG to prove view export (spec §5 P2).

    .venv\\Scripts\\python.exe scripts\\discover_api.py

Output is a human report to stdout (safe — standalone script, not the MCP server).
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

_EXPORT_JPEG = 3  # ExportImage eFormat: 1=BMP 3=JPEG 7=TIFF 9=GIF (8/PNG -> BMP)


def _p(label, fn):
    try:
        v = fn()
        print(f"OK   {label}: {v!r}")
        return v
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {label}: {type(exc).__name__}: {exc}")
        return None


def main() -> int:
    import pythoncom
    pythoncom.CoInitialize()
    try:
        import comtypes.client
        import python_ironcad  # noqa: F401  (triggers typelib extraction; prints banner)
        import comtypes.gen.ICAPIIRONCADLib as ICAPI

        try:
            app = comtypes.client.GetActiveObject("IronCAD.Application")
        except Exception:
            print("Could not attach — is IronCAD 2024 running?")
            traceback.print_exc()
            return 2
        try:
            base = app.QueryInterface(ICAPI.IZBaseApp)
        except Exception:
            print("Attached, but IZBaseApp QI failed — run elevated "
                  "`python -m python_ironcad` to register the API DLLs.")
            traceback.print_exc()
            return 3

        print("== Catalogs (P1) ==")
        cmgr = _p("base.CatalogMgr", lambda: base.CatalogMgr)
        if cmgr is not None:
            count = _p("CatalogMgr.Count", lambda: cmgr.Count)
            for i in range(count or 0):
                try:
                    c = cmgr.Catalog[i]
                    print(f"   Catalog[{i}] {c.Name!r} entries={c.EntryCount}")
                except Exception as exc:  # noqa: BLE001
                    print(f"   Catalog[{i}] FAIL: {exc}")
            active = _p("CatalogMgr.ActiveCatalog", lambda: cmgr.ActiveCatalog)
            if active is not None:
                print(f"   -- entries of {active.Name!r} (first 15) --")
                for j in range(min(active.EntryCount, 15)):
                    e = active.Entry[j]
                    grp = None
                    try:
                        grp = e.IsCatalogGroup()
                    except Exception:  # noqa: BLE001
                        pass
                    print(f"      Entry[{j}] name={e.Name!r} group={grp}")

        print("\n== Active doc + scene (P3/P4/P6) ==")
        doc = _p("base.ActiveDoc", lambda: base.ActiveDoc)
        if doc is not None:
            _p("IZDoc.Name", lambda: doc.Name)
            scene = _p("ActiveDoc.QI(IZSceneDoc)",
                       lambda: doc.QueryInterface(ICAPI.IZSceneDoc))
            if scene is not None:
                _p("scene.GetChildrenElementsCount()", scene.GetChildrenElementsCount)
                try:
                    child = scene.GetFirstChild()
                    idx = 0
                    while child is not None and idx < 30:
                        nm = getattr(child, "Name", "?")
                        ty = getattr(child, "Type", "?")
                        comps = []
                        try:
                            se = child.QueryInterface(ICAPI.IZSceneElement)
                            comps = [round(se.PositionTransformComponentValue[t], 4)
                                     for t in range(3)]
                        except Exception:  # noqa: BLE001
                            comps = ["?"]
                        print(f"   child[{idx}] name={nm!r} type={ty} xyz={comps}")
                        idx += 1
                        child = scene.GetNextChild()
                except Exception as exc:  # noqa: BLE001
                    print("   child enumeration FAIL:", exc)

                print("\n== View export (P2) ==")
                out = os.path.join(tempfile.gettempdir(), "ironcad_discover_view.jpg")
                try:
                    scene.ExportImage(out, _EXPORT_JPEG, 800, 600, True)
                    sz = os.path.getsize(out) if os.path.exists(out) else -1
                    print(f"OK   ExportImage(JPEG) -> {out} ({sz} bytes)")
                except Exception as exc:  # noqa: BLE001
                    print("FAIL ExportImage:", exc)
        print("\n== done ==")
        return 0
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
