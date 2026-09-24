"""Read-only inspection: dump the full 2D cross-section profile of a
PIL4040SNN extrusion post from the live test.ics scene, so we can find the
T-slot channel's real mouth geometry (not infer it from where the user
happened to place a panel by hand).

Does NOT mutate anything.

    .venv\\Scripts\\python.exe scripts\\inspect_pil_profile.py
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

        def dump_profile(el, label):
            print(f"\n=== {label}: {el.Name} ===")
            try:
                se = el.QueryInterface(ICAPI.IZSceneElement)
                pos = [round(float(se.PositionTransformComponentValue[t]), 6) for t in range(3)]
                print(f"pos={pos}")
            except Exception as e:
                print(f"pos err {e}")
            try:
                part = el.QueryInterface(ICAPI.IZPart)
                bb = [round(float(x), 6) for x in part.GetBoundingBox(False)]
                print(f"bbox={bb}")
            except Exception as e:
                print(f"bbox err {e}")

            child = el.GetFirstChild()
            depth = 0
            while child is not None and depth < 8:
                try:
                    fname = str(getattr(child, "Name", "?"))
                    ftype = int(getattr(child, "Type", -1))
                except Exception:
                    fname, ftype = "?", None
                print(f"  [feature] {fname} type={ftype}")
                try:
                    prof = child.QueryInterface(ICAPI.IZProfile)
                    ids = list(prof.GetCurveIds())
                    print(f"    profile curves: {ids}")
                    for cid in ids:
                        try:
                            ctype = prof.GetCurveType(cid)
                            if ctype == 1:
                                ld = prof.GetLineData(cid)

                                def flat(x):
                                    if isinstance(x, (tuple, list)):
                                        out = []
                                        for y in x:
                                            out.extend(flat(y))
                                        return out
                                    return [round(float(x), 6)]

                                print(f"    curve {cid}: LINE {flat(ld)}")
                            else:
                                print(f"    curve {cid}: type={ctype} (non-line)")
                        except Exception as e:
                            print(f"    curve {cid}: <err {e}>")
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
                depth += 1

        print(f"Active doc: {getattr(doc, 'Name', '?')}")
        print("\n== Top-level elements ==")
        c = scene.GetFirstChild()
        idx = 0
        while c is not None:
            try:
                nm = str(c.Name)
            except Exception:
                nm = "?"
            if "PIL" in nm.upper() and idx == 0:
                dump_profile(c, f"element#{idx}")
            idx += 1
            try:
                c = scene.GetNextChild()
            except Exception:
                break

        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
