"""GATED live WRITE test: the "move together" link — AssembleElements / MoveChild.

Tests the VARIANT-array assembly path (API_NOTES §9b) on a SCRATCH scene only.
NEVER touches a saved file (refuses if the active doc has an on-disk path).
Saves nothing. The proof of "parts move together": assemble A+B into an
assembly, move the ASSEMBLY, and confirm BOTH parts' positions shift with it.

    .venv\\Scripts\\python.exe scripts\\test_link_write.py

Run with IronCAD 2024 open on a blank (unsaved) scene.
"""

from __future__ import annotations

import os
import traceback


def _pos(se):
    return [round(float(se.PositionTransformComponentValue[t]), 5) for t in range(3)]


def _find_droppable_entry(cmgr):
    want_cat, want_entry = "PIL_DIST", "PIL4140SNN_"
    for i in range(int(cmgr.Count)):
        cat = cmgr.Catalog[i]
        if str(cat.Name) == want_cat:
            for j in range(int(cat.EntryCount)):
                e = cat.Entry[j]
                if str(e.Name) == want_entry:
                    return cat, e
    for i in range(int(cmgr.Count)):
        cat = cmgr.Catalog[i]
        for j in range(int(cat.EntryCount)):
            e = cat.Entry[j]
            try:
                if not e.IsCatalogGroup():
                    return cat, e
            except Exception:  # noqa: BLE001
                return cat, e
    raise RuntimeError("No droppable catalog entry found.")


def _try_assemble(scene, ICAPI, elA, elB):
    """Attempt AssembleElements with several VARIANT marshalings; return (asm, how)."""
    attempts = []

    def _list():
        return scene.AssembleElements([elA, elB])

    def _tuple():
        return scene.AssembleElements((elA, elB))

    def _safearray():
        from ctypes import POINTER
        from comtypes import IUnknown
        from comtypes.safearray import _midlSAFEARRAY
        sa = _midlSAFEARRAY(POINTER(IUnknown)).create([elA, elB])
        return scene.AssembleElements(sa)

    def _variant():
        from comtypes.automation import VARIANT
        v = VARIANT()
        v.value = [elA, elB]
        return scene.AssembleElements(v)

    for how, fn in (("list", _list), ("tuple", _tuple),
                    ("safearray", _safearray), ("variant", _variant)):
        try:
            asm = fn()
            print(f"OK   AssembleElements via {how}: {asm!r}")
            return asm, how
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL AssembleElements via {how}: {type(exc).__name__}: {exc}")
            attempts.append((how, str(exc)))
    return None, None


def main() -> int:
    import pythoncom
    pythoncom.CoInitialize()
    try:
        import comtypes.client
        import python_ironcad  # noqa: F401
        import comtypes.gen.ICAPIIRONCADLib as ICAPI

        try:
            app = comtypes.client.GetActiveObject("IronCAD.Application")
            base = app.QueryInterface(ICAPI.IZBaseApp)
        except Exception:
            print("Could not attach / QI IZBaseApp.")
            traceback.print_exc()
            return 2

        try:
            app.QueryInterface(ICAPI.IZBaseAppSetups).SilentMode = True
            print("OK   SilentMode = True")
        except Exception as exc:  # noqa: BLE001
            print(f"WARN SilentMode: {exc}")

        doc = base.ActiveDoc
        if doc is None:
            print("No active document. Open a NEW blank scene first.")
            return 4
        name = str(getattr(doc, "Name", "") or "")
        print(f"Active doc: {name!r}")
        if name and os.path.isfile(name):
            print(f"\nREFUSING: active doc is a SAVED file:\n   {name}\n"
                  "Open a new blank scene (File > New), leave it unsaved, re-run.")
            return 5
        print("SAFE: unsaved scratch scene — proceeding.\n")

        scene = doc.QueryInterface(ICAPI.IZSceneDoc)
        cmgr = base.CatalogMgr

        print("== place two fresh parts ==")
        cat, entry = _find_droppable_entry(cmgr)
        print(f"   catalog {cat.Name!r} entry {entry.Name!r}")
        elA = entry.InsertElement()
        elB = entry.InsertElement()
        try:
            elA.Name, elB.Name = "claude_linkA", "claude_linkB"
        except Exception:  # noqa: BLE001
            pass
        seA = elA.QueryInterface(ICAPI.IZSceneElement)
        seB = elB.QueryInterface(ICAPI.IZSceneElement)
        # place them at known, separated spots
        mA = seA.GetPositionTransform(); mA.SetTranslation(0.0, 0.0, 0.0); seA.SetPositionTransform(mA)
        mB = seB.GetPositionTransform(); mB.SetTranslation(0.4, 0.0, 0.0); seB.SetPositionTransform(mB)
        scene.Update()
        posA0, posB0 = _pos(seA), _pos(seB)
        print(f"   A={posA0}  B={posB0}")

        try:
            print(f"   GetShapeRelationsCount (before) = {scene.RelationMgr.GetShapeRelationsCount()}")
        except Exception as exc:  # noqa: BLE001
            print(f"   RelationMgr count (before) FAIL: {exc}")

        print("\n== AssembleElements([A,B]) — VARIANT array path ==")
        asm, how = _try_assemble(scene, ICAPI, elA, elB)

        if asm is not None:
            print("\n== verify grouping: are A and B now CHILDREN of the assembly? ==")
            try:
                asm_el = asm.QueryInterface(ICAPI.IZElement)
                kids = int(asm_el.GetChildrenCount())
                print(f"   assembly.GetChildrenCount() = {kids}")
            except Exception as exc:  # noqa: BLE001
                print(f"   assembly.GetChildrenCount FAIL: {exc}")
            for label, el in (("A", elA), ("B", elB)):
                try:
                    par = el.GetParent()
                    pnm = str(getattr(par, "Name", "?")) if par is not None else None
                    print(f"   {label}.GetParent() = {pnm!r}")
                except Exception as exc:  # noqa: BLE001
                    print(f"   {label}.GetParent FAIL: {exc}")
            try:
                anm = str(getattr(asm_el, "Name", "?"))
                print(f"   assembly name = {anm!r}")
            except Exception:  # noqa: BLE001
                pass
            print("   -> if the assembly has 2 children (A and B parented under it),")
            print("      moving the assembly moves both together (IronCAD grouping).")
            print("\n== move the ASSEMBLY +0.25 in Y (visual confirmation) ==")
            try:
                se_asm = asm.QueryInterface(ICAPI.IZSceneElement)
                m = se_asm.GetPositionTransform()
                m.SetTranslation(0.0, 0.25, 0.0)
                se_asm.SetPositionTransform(m)
                scene.Update()
                print("   OK assembly moved (check IronCAD: both parts shift together).")
            except Exception as exc:  # noqa: BLE001
                print(f"   assembly move FAIL: {type(exc).__name__}: {exc}")
        else:
            print("\n== AssembleElements unavailable via any marshaling — try MoveChild ==")
            # Re-parent B under A (A becomes the new parent). Then move A; B should follow.
            try:
                # MoveChild is on the NEW-PARENT object; try on A's element.
                elA.MoveChild(elB, True)
                scene.Update()
                mA2 = seA.GetPositionTransform(); mA2.SetTranslation(0.0, 0.25, 0.0)
                seA.SetPositionTransform(mA2); scene.Update()
                posA1, posB1 = _pos(seA), _pos(seB)
                print(f"   A: {posA0} -> {posA1}")
                print(f"   B: {posB0} -> {posB1}")
                print("   (if B followed A, MoveChild re-parenting works)")
            except Exception as exc:  # noqa: BLE001
                print(f"   MoveChild FAIL: {type(exc).__name__}: {exc}")

        try:
            print(f"\n   GetShapeRelationsCount (after) = {scene.RelationMgr.GetShapeRelationsCount()}")
        except Exception as exc:  # noqa: BLE001
            print(f"   RelationMgr count (after) FAIL: {exc}")

        print("\n== RESULT: nothing saved. Undo/close-without-save to discard. ==")
        print("== done ==")
        return 0
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
