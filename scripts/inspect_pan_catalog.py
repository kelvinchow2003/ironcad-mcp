"""Read-only: list PAN_DIST catalog entries whose name mentions PAN or PIN, to
find the exact InsertElement()-able name for the auto-fit panel-with-pin combo
seen live in the user's scene (assembly named 'PAN____P_N____ + PIN4521' with
settable Gesamtbreite/Gesamtlaenge params).

    .venv\\Scripts\\python.exe scripts\\inspect_pan_catalog.py
"""
from __future__ import annotations
import traceback


def main() -> int:
    import pythoncom
    pythoncom.CoInitialize()
    try:
        import comtypes.client
        import python_ironcad  # noqa: F401
        import comtypes.gen.ICAPIIRONCADLib as ICAPI

        app = comtypes.client.GetActiveObject("IronCAD.Application")
        base = app.QueryInterface(ICAPI.IZBaseApp)
        cm = base.CatalogMgr
        n = int(cm.Count)
        for i in range(n):
            cat = cm.Catalog[i]
            cname = str(cat.Name)
            if "PAN" not in cname.upper():
                continue
            cnt = int(cat.EntryCount)
            print(f"\nCatalog '{cname}' ({cnt} entries):")
            for j in range(cnt):
                entry = cat.Entry[j]
                ename = str(entry.Name)
                if "PAN" in ename.upper() or "PIN" in ename.upper():
                    print(f"  [{j}] {ename!r}")
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
