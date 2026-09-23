"""Read-only inspection spike: how did the user manually fit a panel in the
CURRENT live scene? Dumps every top-level element's name/type/bbox/position/
rotation, and for anything panel-like (PAN___ prefix or has a 'Dicke' param),
drills into its feature tree (Block -> 2D sketch) to read real geometry.

Does NOT mutate anything. Safe to run against the user's live, unsaved work.

    .venv\\Scripts\\python.exe scripts\\inspect_panel_fit.py
"""

from __future__ import annotations

import traceback


def _members(obj):
    try:
        return sorted(m for m in dir(obj) if not m.startswith("__"))
    except Exception:
        return []


def main() -> int:
    import pythoncom
    pythoncom.CoInitialize()
    try:
        import comtypes.client
        import python_ironcad  # noqa: F401
        import comtypes.gen.ICAPIIRONCADLib as ICAPI

        app = comtypes.client.GetActiveObject("IronCAD.Application")
        base = app.QueryInterface(ICAPI.IZBaseApp)
        doc = base.ActiveDoc
        if doc is None:
            print("No active document.")
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

            # Drill into feature tree for anything panel-like or if this is a leaf part.
            if "PAN" in nm.upper() or "Dicke" in prm or etype == 1:
                try:
                    child = el.GetFirstChild()
                except Exception:
                    child = None
                fdepth = 0
                while child is not None and fdepth < 6:
                    try:
                        fname = str(getattr(child, "Name", "?"))
                    except Exception:
                        fname = "?"
                    try:
                        ftype = int(getattr(child, "Type", -1))
                    except Exception:
                        ftype = None
                    print(f"{indent}    [feature] {fname} type={ftype}")
                    # If it's a profile/sketch, dump curve geometry.
                    try:
                        prof = child.QueryInterface(ICAPI.IZProfile)
                        ids = prof.GetCurveIds()
                        print(f"{indent}      profile curves: {list(ids)}")
                        for cid in list(ids)[:20]:
                            try:
                                ctype = prof.GetCurveType(cid)
                                ld = prof.GetLineData(cid) if ctype == 1 else None
                                print(f"{indent}        curve {cid}: type={ctype} line={ld}")
                            except Exception as e:
                                print(f"{indent}        curve {cid}: <err {e}>")
                    except Exception:
                        pass
                    # If it's an extrude feature, dump distance.
                    try:
                        ef = child.QueryInterface(ICAPI.IZExtrudeFeature)
                        try:
                            d0 = ef.ExtrudeDistance(0)
                        except Exception:
                            d0 = None
                        try:
                            d1 = ef.ExtrudeDistance(1)
                        except Exception:
                            d1 = None
                        print(f"{indent}      extrude distances fwd={d0} bwd={d1}")
                    except Exception:
                        pass
                    try:
                        nxt = child.GetNextChild()
                    except Exception:
                        nxt = None
                    if nxt is None:
                        try:
                            nxt = child.GetFirstChild()
                        except Exception:
                            nxt = None
                    child = nxt
                    fdepth += 1

            # Recurse into assembly children.
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
