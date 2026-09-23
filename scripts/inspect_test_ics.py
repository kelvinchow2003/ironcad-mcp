"""Read-only inspection of test.ics specifically, by name, regardless of which
tab is currently focused in IronCAD. Does NOT mutate anything.

    .venv\\Scripts\\python.exe scripts\\inspect_test_ics.py
"""

from __future__ import annotations

import traceback

TARGET = r"C:\Users\admin\Downloads\test.ics"


def main() -> int:
    import pythoncom
    pythoncom.CoInitialize()
    try:
        import comtypes.client
        import python_ironcad  # noqa: F401
        import comtypes.gen.ICAPIIRONCADLib as ICAPI

        app = comtypes.client.GetActiveObject("IronCAD.Application")
        base = app.QueryInterface(ICAPI.IZBaseApp)

        doc = base.GetOpenDocByName(TARGET)
        if doc is None:
            print(f"NOT FOUND OPEN: {TARGET}")
            return 4
        scene = doc.QueryInterface(ICAPI.IZSceneDoc)
        print(f"Active doc: {getattr(doc, 'Name', '?')}")

        def bbox_of(el):
            try:
                part = el.QueryInterface(ICAPI.IZPart)
                v = part.GetBoundingBox(False)
                return [round(float(x), 6) for x in v]
            except Exception:
                return None

        def pos_of(el):
            try:
                se = el.QueryInterface(ICAPI.IZSceneElement)
                return [round(float(se.PositionTransformComponentValue[t]), 6) for t in range(3)]
            except Exception:
                return None

        def params_of(el):
            out = {}
            try:
                se = el.QueryInterface(ICAPI.IZSceneElement)
                pmgr = se.ParameterMgr
                cnt = int(pmgr.Count)
                for i in range(min(cnt, 50)):
                    try:
                        p = pmgr.GetParameter(i)
                        out[str(p.Name)] = getattr(p, "Value", None)
                    except Exception:
                        continue
            except Exception:
                pass
            return out

        def walk(el, depth, path):
            indent = "  " * depth
            try:
                nm = str(el.Name)
            except Exception:
                nm = "<?>"
            try:
                etype = int(el.Type)
            except Exception:
                etype = None
            bb = bbox_of(el)
            pos = pos_of(el)
            prm = params_of(el)
            print(f"{indent}- {nm}  type={etype}  pos={pos}  bbox={bb}  params={prm}")

            if etype == 2:
                try:
                    kids = el.GetChildren()
                except Exception:
                    kids = None
                if kids:
                    for k in list(kids):
                        walk(k, depth + 1, path + [nm])

        print("\n== Top-level elements ==")
        child = scene.GetFirstChild()
        idx = 0
        while child is not None:
            walk(child, 0, [])
            idx += 1
            try:
                child = scene.GetNextChild()
            except Exception:
                break

        print(f"\nTotal top-level: {idx}")
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
